// This Source Code Form is subject to the terms of the bCDDL, v. 1.1.
// If a copy of the bCDDL was not distributed with this
// file, You can obtain one at http://beamng.com/bCDDL-1.1.txt

#include "BngVehicleMeshRender.h"

#include "BngVehicleSkinShader.h"
#include "RenderGraphUtils.h"
#include "Materials/Material.h"
#include "MeshBatch.h"
#include "PrimitiveUniformShaderParametersBuilder.h"
#include "PrimitiveViewRelevance.h"
#include "SceneManagement.h"

namespace
{
    template <typename T>
    TArray<uint8> ToBytes(const TArray<T>& Values)
    {
        return TArray<uint8>(reinterpret_cast<const uint8*>(Values.GetData()), Values.Num() * sizeof(T));
    }

    FBufferRHIRef CreateBuffer(FRHICommandListBase& RHICmdList, const TCHAR* Name, uint32 Size, uint32 Stride,
        EBufferUsageFlags Usage, ERHIAccess InitialState)
    {
        return RHICmdList.CreateBuffer(
            FRHIBufferCreateDesc::Create(Name, Size, Stride, Usage)
            .SetInitialState(InitialState)
            .SetInitActionNone());
    }

    void UploadBuffer(FRHICommandListBase& RHICmdList, FRHIBuffer* Buffer, const void* Data, uint32 Size)
    {
        if (Size == 0)
        {
            return;
        }

        void* Dest = RHICmdList.LockBuffer(Buffer, 0, Size, RLM_WriteOnly);
        FMemory::Memcpy(Dest, Data, Size);
        RHICmdList.UnlockBuffer(Buffer);
    }

    FShaderResourceViewRHIRef CreateTypedSRV(FRHICommandListBase& RHICmdList, FRHIBuffer* Buffer,
        EPixelFormat Format, uint32 NumElements)
    {
        return RHICmdList.CreateShaderResourceView(Buffer,
            FRHIViewDesc::CreateBufferSRV()
            .SetType(FRHIViewDesc::EBufferType::Typed)
            .SetFormat(Format)
            .SetNumElements(NumElements));
    }

    FUnorderedAccessViewRHIRef CreateTypedUAV(FRHICommandListBase& RHICmdList, FRHIBuffer* Buffer,
        EPixelFormat Format, uint32 NumElements)
    {
        return RHICmdList.CreateUnorderedAccessView(Buffer,
            FRHIViewDesc::CreateBufferUAV()
            .SetType(FRHIViewDesc::EBufferType::Typed)
            .SetFormat(Format)
            .SetNumElements(NumElements));
    }

    FShaderResourceViewRHIRef CreateStructuredSRV(FRHICommandListBase& RHICmdList, FRHIBuffer* Buffer,
        uint32 Stride, uint32 NumElements)
    {
        return RHICmdList.CreateShaderResourceView(Buffer,
            FRHIViewDesc::CreateBufferSRV()
            .SetType(FRHIViewDesc::EBufferType::Structured)
            .SetStride(Stride)
            .SetNumElements(NumElements));
    }
}

FBngVertexBuffer::FBngVertexBuffer(const TCHAR* InName, EBufferUsageFlags InUsage, EPixelFormat InSrvFormat,
    EPixelFormat InUavFormat)
    : Name(InName)
    , Usage(InUsage)
    , SrvFormat(InSrvFormat)
    , UavFormat(InUavFormat)
{
}

void FBngVertexBuffer::SetData(TArray<uint8>&& InData, uint32 InStride)
{
    InitialData = MoveTemp(InData);
    Size = InitialData.Num();
    Stride = InStride;
}

void FBngVertexBuffer::InitRHI(FRHICommandListBase& RHICmdList)
{
    VertexBufferRHI = CreateBuffer(RHICmdList, Name, Size, Stride, Usage, ERHIAccess::VertexOrIndexBuffer | ERHIAccess::SRVMask);
    UploadBuffer(RHICmdList, VertexBufferRHI, InitialData.GetData(), Size);

    if (SrvFormat != PF_Unknown)
    {
        const int32 BlockBytes = GPixelFormats[SrvFormat].BlockBytes;
        SRV = CreateTypedSRV(RHICmdList, VertexBufferRHI, SrvFormat, Size / BlockBytes);
    }
    if (UavFormat != PF_Unknown)
    {
        const int32 BlockBytes = GPixelFormats[UavFormat].BlockBytes;
        UAV = CreateTypedUAV(RHICmdList, VertexBufferRHI, UavFormat, Size / BlockBytes);
    }
}

void FBngVertexBuffer::ReleaseRHI()
{
    SRV.SafeRelease();
    UAV.SafeRelease();
    FVertexBuffer::ReleaseRHI();
}

FString FBngVertexBuffer::GetFriendlyName() const
{
    return Name;
}

FBngVehicleMeshResources::FBngVehicleMeshResources(ERHIFeatureLevel::Type FeatureLevel, TArray<FVector3f>&& Positions,
    TArray<uint32>&& Tangents, TArray<FVector4f>&& UVs, TArray<uint32>&& Indices, const TArray<FBngVehicleMeshSection>& InSections)
    : VertexCount(Positions.Num())
    , Sections(InSections)
    , PositionBuffer(TEXT("BngVehiclePositions"), BUF_VertexBuffer | BUF_ShaderResource | BUF_UnorderedAccess, PF_R32_FLOAT, PF_R32_FLOAT)
    , TangentBuffer(TEXT("BngVehicleTangents"), BUF_VertexBuffer | BUF_ShaderResource | BUF_UnorderedAccess, PF_R8G8B8A8_SNORM, PF_R32_UINT)
    , TexCoordBuffer(TEXT("BngVehicleTexCoords"), BUF_VertexBuffer | BUF_ShaderResource, PF_G32R32F)
    , VertexFactory(FeatureLevel, "FBngVehicleMeshResources")
    , FeatureLevel(FeatureLevel)
{
    PositionBuffer.SetData(ToBytes(Positions), sizeof(FVector3f));
    TangentBuffer.SetData(ToBytes(Tangents), sizeof(uint32));
    TexCoordBuffer.SetData(ToBytes(UVs), sizeof(FVector4f));
    IndexBuffer.Indices = MoveTemp(Indices);
    IndexBuffer.UsageFlags = EBufferUsageFlags::Dynamic;
    ColorBuffer.InitFromSingleColor(FColor::White, VertexCount);

    BeginInitResource(&PositionBuffer);
    BeginInitResource(&TangentBuffer);
    BeginInitResource(&TexCoordBuffer);
    BeginInitResource(&ColorBuffer);
    BeginInitResource(&IndexBuffer);

    ENQUEUE_RENDER_COMMAND(BngLinkInitVehicleVertexFactory)(
        [this](FRHICommandListImmediate& RHICmdList)
        {
            FLocalVertexFactory::FDataType Data;
            Data.PositionComponent = FVertexStreamComponent(&PositionBuffer, 0, sizeof(FVector3f), VET_Float3);
            Data.PositionComponentSRV = PositionBuffer.SRV;
            Data.TangentBasisComponents[0] = FVertexStreamComponent(&TangentBuffer, 0, sizeof(uint32) * 2, VET_PackedNormal, EVertexStreamUsage::ManualFetch);
            Data.TangentBasisComponents[1] = FVertexStreamComponent(&TangentBuffer, sizeof(uint32), sizeof(uint32) * 2, VET_PackedNormal, EVertexStreamUsage::ManualFetch);
            Data.TangentsSRV = TangentBuffer.SRV;
            Data.TextureCoordinates.Add(FVertexStreamComponent(&TexCoordBuffer, 0, sizeof(FVector4f), VET_Float2, EVertexStreamUsage::ManualFetch));
            Data.TextureCoordinates.Add(FVertexStreamComponent(&TexCoordBuffer, sizeof(FVector2f), sizeof(FVector4f), VET_Float2, EVertexStreamUsage::ManualFetch));
            Data.TextureCoordinatesSRV = TexCoordBuffer.SRV;
            Data.NumTexCoords = 2;
            Data.LightMapCoordinateIndex = 1;
            Data.LightMapCoordinateComponent = Data.TextureCoordinates[1];
            ColorBuffer.BindColorVertexBuffer(&VertexFactory, Data);
            VertexFactory.SetData(RHICmdList, Data);
        });
    BeginInitResource(&VertexFactory);
}

FBngVehicleMeshResources::~FBngVehicleMeshResources()
{
    check(IsInRenderingThread());
    VertexFactory.ReleaseResource();
    IndexBuffer.ReleaseResource();
    ColorBuffer.ReleaseResource();
    TexCoordBuffer.ReleaseResource();
    TangentBuffer.ReleaseResource();
    PositionBuffer.ReleaseResource();
}

void FBngVehicleMeshResources::BindSkin_RenderThread(FRHICommandListImmediate& RHICmdList,
    TArray<FBngSkinVertex>&& SkinVertices, int32 NodeCount)
{
    if (SkinVertices.Num() != VertexCount || NodeCount <= 0)
    {
        return;
    }

    SkinNodeCount = NodeCount;
    const uint32 SkinSize = SkinVertices.Num() * sizeof(FBngSkinVertex);
    SkinBuffer = CreateBuffer(RHICmdList, TEXT("BngVehicleSkin"), SkinSize, sizeof(FBngSkinVertex),
        BUF_StructuredBuffer | BUF_ShaderResource, ERHIAccess::SRVMask);
    UploadBuffer(RHICmdList, SkinBuffer, SkinVertices.GetData(), SkinSize);
    SkinSRV = CreateStructuredSRV(RHICmdList, SkinBuffer, sizeof(FBngSkinVertex), SkinVertices.Num());

    const uint32 NodeSize = NodeCount * sizeof(FVector3f);
    NodeBuffer = CreateBuffer(RHICmdList, TEXT("BngVehicleNodes"), NodeSize, sizeof(FVector3f),
        BUF_StructuredBuffer | BUF_ShaderResource, ERHIAccess::SRVMask);
    NodeSRV = CreateStructuredSRV(RHICmdList, NodeBuffer, sizeof(FVector3f), NodeCount);
}

void FBngVehicleMeshResources::Skin_RenderThread(FRHICommandListImmediate& RHICmdList, const TArray<FVector3f>& Nodes)
{
    if (!SkinSRV || !NodeSRV || Nodes.Num() != SkinNodeCount)
    {
        return;
    }

    const uint32 NodeSize = Nodes.Num() * sizeof(FVector3f);
    UploadBuffer(RHICmdList, NodeBuffer, Nodes.GetData(), NodeSize);

    RHICmdList.Transition(FRHITransitionInfo(PositionBuffer.VertexBufferRHI,
        ERHIAccess::VertexOrIndexBuffer | ERHIAccess::SRVMask, ERHIAccess::UAVCompute));
    RHICmdList.Transition(FRHITransitionInfo(TangentBuffer.VertexBufferRHI,
        ERHIAccess::VertexOrIndexBuffer | ERHIAccess::SRVMask, ERHIAccess::UAVCompute));

    auto ComputeShader = GetGlobalShaderMap(FeatureLevel)->GetShader<FBngVehicleSkinCS>();
    FBngVehicleSkinCS::FParameters Parameters;
    Parameters.VertexCount = VertexCount;
    Parameters.Nodes = NodeSRV;
    Parameters.SkinVertices = SkinSRV;
    Parameters.OutPositions = PositionBuffer.UAV;
    Parameters.OutTangents = TangentBuffer.UAV;
    FComputeShaderUtils::Dispatch(RHICmdList, ComputeShader, Parameters, FIntVector(FMath::DivideAndRoundUp(VertexCount, 64), 1, 1));

    RHICmdList.Transition(FRHITransitionInfo(PositionBuffer.VertexBufferRHI,
        ERHIAccess::UAVCompute, ERHIAccess::VertexOrIndexBuffer | ERHIAccess::SRVMask));
    RHICmdList.Transition(FRHITransitionInfo(TangentBuffer.VertexBufferRHI,
        ERHIAccess::UAVCompute, ERHIAccess::VertexOrIndexBuffer | ERHIAccess::SRVMask));
}

void FBngVehicleMeshResources::UpdateIndices_RenderThread(
    FRHICommandListImmediate& RHICmdList, const TArray<uint32>& Indices)
{
    if (Indices.Num() != IndexBuffer.Indices.Num())
    {
        return;
    }

    UploadBuffer(RHICmdList, IndexBuffer.IndexBufferRHI, Indices.GetData(),
        Indices.Num() * sizeof(uint32));
}

FBngVehicleMeshSceneProxy::FBngVehicleMeshSceneProxy(const UBngVehicleMeshComponent* Component,
    const TSharedPtr<FBngVehicleMeshResources, ESPMode::ThreadSafe>& InResources)
    : FPrimitiveSceneProxy(Component)
    , Resources(InResources)
    , MaterialRelevance(Component->GetMaterialRelevance(GetScene().GetShaderPlatform()))
{
    Materials.Reserve(Resources->Sections.Num());
    for (int32 SectionIndex = 0; SectionIndex < Resources->Sections.Num(); ++SectionIndex)
    {
        UMaterialInterface* Material = Component->GetMaterial(SectionIndex);
        Materials.Add(Material ? Material : UMaterial::GetDefaultMaterial(MD_Surface));
    }
}

SIZE_T FBngVehicleMeshSceneProxy::GetTypeHash() const
{
    static size_t UniquePointer;
    return reinterpret_cast<size_t>(&UniquePointer);
}

void FBngVehicleMeshSceneProxy::GetDynamicMeshElements(const TArray<const FSceneView*>& Views,
    const FSceneViewFamily& ViewFamily, uint32 VisibilityMap, FMeshElementCollector& Collector) const
{
    for (int32 ViewIndex = 0; ViewIndex < Views.Num(); ++ViewIndex)
    {
        if (!(VisibilityMap & (1 << ViewIndex)))
        {
            continue;
        }

        FDynamicPrimitiveUniformBuffer& DynamicPrimitiveUniformBuffer = Collector.AllocateOneFrameResource<FDynamicPrimitiveUniformBuffer>();
        FPrimitiveUniformShaderParametersBuilder Builder;
        BuildUniformShaderParameters(Builder);
        DynamicPrimitiveUniformBuffer.Set(Collector.GetRHICommandList(), Builder);

        for (int32 SectionIndex = 0; SectionIndex < Resources->Sections.Num(); ++SectionIndex)
        {
            const FBngVehicleMeshSection& Section = Resources->Sections[SectionIndex];

            FMeshBatch& Mesh = Collector.AllocateMesh();
            Mesh.VertexFactory = &Resources->VertexFactory;
            Mesh.MaterialRenderProxy = Materials[SectionIndex]->GetRenderProxy();
            Mesh.ReverseCulling = IsLocalToWorldDeterminantNegative();
            Mesh.Type = PT_TriangleList;
            Mesh.DepthPriorityGroup = SDPG_World;
            Mesh.bCanApplyViewModeOverrides = true;

            FMeshBatchElement& Element = Mesh.Elements[0];
            Element.IndexBuffer = &Resources->IndexBuffer;
            Element.FirstIndex = Section.FirstIndex;
            Element.NumPrimitives = Section.NumTriangles;
            Element.MinVertexIndex = Section.MinVertexIndex;
            Element.MaxVertexIndex = Section.MaxVertexIndex;
            Element.PrimitiveUniformBufferResource = &DynamicPrimitiveUniformBuffer.UniformBuffer;

            Collector.AddMesh(ViewIndex, Mesh);
        }
    }
}

FPrimitiveViewRelevance FBngVehicleMeshSceneProxy::GetViewRelevance(const FSceneView* View) const
{
    FPrimitiveViewRelevance Result;
    Result.bDrawRelevance = IsShown(View);
    Result.bShadowRelevance = IsShadowCast(View);
    Result.bDynamicRelevance = true;
    Result.bRenderInMainPass = ShouldRenderInMainPass();
    Result.bRenderCustomDepth = ShouldRenderCustomDepth();
    Result.bUsesLightingChannels = GetLightingChannelMask() != GetDefaultLightingChannelMask();
    Result.bTranslucentSelfShadow = bCastVolumetricTranslucentShadow;
    MaterialRelevance.SetPrimitiveViewRelevance(Result);
    Result.bVelocityRelevance = DrawsVelocity() && Result.bOpaque && Result.bRenderInMainPass;
    return Result;
}

uint32 FBngVehicleMeshSceneProxy::GetMemoryFootprint() const
{
    return sizeof(*this) + GetAllocatedSize();
}
