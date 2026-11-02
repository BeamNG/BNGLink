// This Source Code Form is subject to the terms of the bCDDL, v. 1.1.
// If a copy of the bCDDL was not distributed with this
// file, You can obtain one at http://beamng.com/bCDDL-1.1.txt

#include "BngVehicleMeshComponent.h"

#include "BngVehicleMeshRender.h"
#include "PackedNormal.h"

namespace
{
    constexpr double BoundsPadding = 100.0;

    FBox PaddedBounds(const FVector3f* Positions, int32 Count)
    {
        FBox Bounds(ForceInit);
        for (int32 Index = 0; Index < Count; ++Index)
        {
            Bounds += FVector(Positions[Index]);
        }
        return Bounds.ExpandBy(BoundsPadding);
    }

    void ExpandBoundsWithNodes(FBox& Bounds, const TArray<float>& Nodes)
    {
        FBox NodeBounds(ForceInit);
        for (int32 Index = 0; Index + 2 < Nodes.Num(); Index += 3)
        {
            NodeBounds += FVector(Nodes[Index], Nodes[Index + 1], Nodes[Index + 2]);
        }
        if (NodeBounds.IsValid)
        {
            Bounds += NodeBounds.ExpandBy(BoundsPadding);
        }
    }

    bool SectionsAreValid(const TArray<FBngVehicleMeshSection>& Sections, int32 VertexCount, int32 IndexCount)
    {
        for (const FBngVehicleMeshSection& Section : Sections)
        {
            if (Section.FirstIndex < 0 || Section.NumTriangles <= 0
                || Section.FirstIndex + Section.NumTriangles * 3 > IndexCount
                || Section.MinVertexIndex < 0 || Section.MinVertexIndex > Section.MaxVertexIndex
                || Section.MaxVertexIndex >= VertexCount)
            {
                return false;
            }
        }
        return true;
    }

    uint32 PackNormal(const FVector4f& Vector)
    {
        return FPackedNormal(Vector).Vector.Packed;
    }

    template <typename T>
    void CopyBytes(TArray<uint8>& Dest, const T* Source, int32 Count)
    {
        Dest.SetNumUninitialized(Count * sizeof(T));
        FMemory::Memcpy(Dest.GetData(), Source, Dest.Num());
    }

    template <typename T>
    TArray<T> TypedCopy(const TArray<uint8>& Source)
    {
        return TArray<T>(reinterpret_cast<const T*>(Source.GetData()), Source.Num() / sizeof(T));
    }

    int32 UpperBound(const TArray<double>& Values, double Value)
    {
        int32 First = 0;
        int32 Count = Values.Num();
        while (Count > 0)
        {
            const int32 Step = Count / 2;
            const int32 Index = First + Step;
            if (Values[Index] <= Value)
            {
                First = Index + 1;
                Count -= Step + 1;
            }
            else
            {
                Count = Step;
            }
        }
        return First;
    }

    void DeleteResourcesOnRenderThread(FBngVehicleMeshResources* Resources)
    {
        if (IsInRenderingThread())
        {
            delete Resources;
            return;
        }

        ENQUEUE_RENDER_COMMAND(BngLinkDeleteVehicleMesh)(
            [Resources](FRHICommandListImmediate&) { delete Resources; });
    }
}

UBngVehicleMeshComponent::UBngVehicleMeshComponent()
{
    PrimaryComponentTick.bCanEverTick = false;
    ShadowCacheInvalidationBehavior = EShadowCacheInvalidationBehavior::Always;
}

bool UBngVehicleMeshComponent::InitMesh(int32 VertexCount, int64 PositionsAddress, int64 NormalsAddress,
    int64 TangentsAddress, int64 UVsAddress, int32 IndexCount, int64 IndicesAddress,
    const TArray<FBngVehicleMeshSection>& Sections)
{
    if (VertexCount <= 0 || IndexCount <= 0 || !PositionsAddress || !NormalsAddress || !TangentsAddress
        || !UVsAddress || !IndicesAddress || Sections.IsEmpty() || !SectionsAreValid(Sections, VertexCount, IndexCount))
    {
        return false;
    }

    const FVector3f* Positions = reinterpret_cast<const FVector3f*>(PositionsAddress);
    const FVector3f* Normals = reinterpret_cast<const FVector3f*>(NormalsAddress);
    const FVector4f* Tangents = reinterpret_cast<const FVector4f*>(TangentsAddress);
    const FVector4f* UVs = reinterpret_cast<const FVector4f*>(UVsAddress);

    TArray<uint32> TangentData;
    TangentData.SetNumUninitialized(VertexCount * 2);
    for (int32 Index = 0; Index < VertexCount; ++Index)
    {
        const float BinormalSign = Tangents[Index].W < 0.0f ? -1.0f : 1.0f;
        TangentData[Index * 2] = PackNormal(FVector4f(Tangents[Index].X, Tangents[Index].Y, Tangents[Index].Z, 0.0f));
        TangentData[Index * 2 + 1] = PackNormal(FVector4f(Normals[Index].X, Normals[Index].Y, Normals[Index].Z, BinormalSign));
    }

    CopyBytes(MeshPositions, Positions, VertexCount);
    CopyBytes(MeshTangents, TangentData.GetData(), TangentData.Num());
    CopyBytes(MeshUVs, UVs, VertexCount);
    MeshIndices = TArray<uint32>(reinterpret_cast<const uint32*>(IndicesAddress), IndexCount);
    MeshSections = Sections;
    return CreateResources();
}

bool UBngVehicleMeshComponent::UpdateIndices(int32 IndexCount, int64 IndicesAddress)
{
    if (IndexCount != MeshIndices.Num() || !IndicesAddress)
    {
        return false;
    }

    MeshIndices = TArray<uint32>(reinterpret_cast<const uint32*>(IndicesAddress), IndexCount);
    UpdateIndexBuffer();
    return true;
}

bool UBngVehicleMeshComponent::BindSkin(int32 NodeCount, int64 SkinVerticesAddress, int32 VertexCount)
{
    if (!Resources.IsValid() || NodeCount <= 0 || !SkinVerticesAddress || VertexCount != Resources->VertexCount)
    {
        return false;
    }

    const FBngSkinVertex* SkinData = reinterpret_cast<const FBngSkinVertex*>(SkinVerticesAddress);
    CopyBytes(SkinVertices, SkinData, VertexCount);
    SkinNodeCount = NodeCount;
    BindStoredSkin();
    return true;
}

bool UBngVehicleMeshComponent::SetTake(double Duration, int32 SampleCount, int32 NodeCount,
    int64 TimesAddress, int64 NodesAddress, int64 RotationsAddress, int64 TranslationsAddress)
{
    if (Duration < 0.0 || SampleCount <= 0 || NodeCount <= 0 || !TimesAddress || !NodesAddress
        || !RotationsAddress || !TranslationsAddress)
    {
        return false;
    }

    const int64 NodeSampleCount = static_cast<int64>(SampleCount) * NodeCount;
    const int64 RotationValueCount = static_cast<int64>(SampleCount) * 4;
    const int64 TranslationValueCount = static_cast<int64>(SampleCount) * 3;
    if (NodeSampleCount > MAX_int32 / 3 || RotationValueCount > MAX_int32
        || TranslationValueCount > MAX_int32)
    {
        return false;
    }
    const int32 NodeValueCount = static_cast<int32>(NodeSampleCount * 3);

    const double* Times = reinterpret_cast<const double*>(TimesAddress);
    for (int32 Index = 1; Index < SampleCount; ++Index)
    {
        if (Times[Index] <= Times[Index - 1])
        {
            return false;
        }
    }

    TakeDuration = FMath::Max(Duration, Times[SampleCount - 1]);
    TakeNodeCount = NodeCount;
    TakeTimes = TArray<double>(Times, SampleCount);

    TakeNodes.SetNumUninitialized(NodeValueCount);
    FMemory::Memcpy(TakeNodes.GetData(), reinterpret_cast<const float*>(NodesAddress), TakeNodes.Num() * sizeof(float));
    TakeRotations.SetNumUninitialized(static_cast<int32>(RotationValueCount));
    FMemory::Memcpy(TakeRotations.GetData(), reinterpret_cast<const float*>(RotationsAddress), TakeRotations.Num() * sizeof(float));
    TakeTranslations.SetNumUninitialized(static_cast<int32>(TranslationValueCount));
    FMemory::Memcpy(TakeTranslations.GetData(), reinterpret_cast<const float*>(TranslationsAddress), TakeTranslations.Num() * sizeof(float));
    InterpolatedNodes.SetNumUninitialized(NodeCount);

    ExpandBoundsWithNodes(LocalBounds, TakeNodes);
    UpdateBounds();
    MarkRenderTransformDirty();

    SetTime(Time);
    return true;
}

bool UBngVehicleMeshComponent::SetTakeIndices(int32 SampleCount, int32 IndexCount,
    int64 TimesAddress, int64 IndicesAddress)
{
    if (SampleCount <= 0 || IndexCount != MeshIndices.Num() || !TimesAddress || !IndicesAddress
        || static_cast<int64>(SampleCount) * IndexCount > MAX_int32)
    {
        return false;
    }

    const double* Times = reinterpret_cast<const double*>(TimesAddress);
    for (int32 Index = 1; Index < SampleCount; ++Index)
    {
        if (Times[Index] <= Times[Index - 1])
        {
            return false;
        }
    }

    const uint32* Indices = reinterpret_cast<const uint32*>(IndicesAddress);
    const int32 ValueCount = SampleCount * IndexCount;
    TakeIndexTimes = TArray<double>(Times, SampleCount);
    TakeIndices = TArray<uint32>(Indices, ValueCount);
    TakeIndexSample = -1;
    SetTime(Time);
    return true;
}

void UBngVehicleMeshComponent::SetTime(double InTime)
{
    Time = FMath::Clamp(InTime, 0.0, TakeDuration);
    if (!TakeIndexTimes.IsEmpty())
    {
        const int32 Sample = FMath::Clamp(UpperBound(TakeIndexTimes, Time) - 1, 0, TakeIndexTimes.Num() - 1);
        if (Sample != TakeIndexSample)
        {
            TakeIndexSample = Sample;
            const int32 IndexCount = MeshIndices.Num();
            FMemory::Memcpy(
                MeshIndices.GetData(),
                TakeIndices.GetData() + Sample * IndexCount,
                IndexCount * sizeof(uint32));
            UpdateIndexBuffer();
        }
    }
    if (TakeTimes.IsEmpty())
    {
        return;
    }

    const int32 After = UpperBound(TakeTimes, Time);
    const int32 First = FMath::Clamp(After - 1, 0, TakeTimes.Num() - 1);
    const int32 Second = FMath::Min(First + 1, TakeTimes.Num() - 1);
    const double Interval = TakeTimes[Second] - TakeTimes[First];
    const float Alpha = Interval > 0.0
        ? FMath::Clamp(static_cast<float>((Time - TakeTimes[First]) / Interval), 0.0f, 1.0f)
        : 0.0f;

    InterpolatedNodes.SetNumUninitialized(TakeNodeCount);
    const float* FirstNodes = TakeNodes.GetData() + First * TakeNodeCount * 3;
    const float* SecondNodes = TakeNodes.GetData() + Second * TakeNodeCount * 3;
    for (int32 Index = 0; Index < TakeNodeCount; ++Index)
    {
        InterpolatedNodes[Index] = FMath::Lerp(
            FVector3f(FirstNodes[Index * 3], FirstNodes[Index * 3 + 1], FirstNodes[Index * 3 + 2]),
            FVector3f(SecondNodes[Index * 3], SecondNodes[Index * 3 + 1], SecondNodes[Index * 3 + 2]),
            Alpha);
    }

    const float* FirstRotation = TakeRotations.GetData() + First * 4;
    const float* SecondRotation = TakeRotations.GetData() + Second * 4;
    const FQuat Rotation = FQuat::Slerp(
        FQuat(FirstRotation[0], FirstRotation[1], FirstRotation[2], FirstRotation[3]),
        FQuat(SecondRotation[0], SecondRotation[1], SecondRotation[2], SecondRotation[3]),
        Alpha).GetNormalized();

    const float* FirstTranslation = TakeTranslations.GetData() + First * 3;
    const float* SecondTranslation = TakeTranslations.GetData() + Second * 3;
    const FVector Translation = FMath::Lerp(
        FVector(FirstTranslation[0], FirstTranslation[1], FirstTranslation[2]),
        FVector(SecondTranslation[0], SecondTranslation[1], SecondTranslation[2]),
        Alpha);

    UpdateMesh(Rotation, Translation, reinterpret_cast<int64>(InterpolatedNodes.GetData()), TakeNodeCount);
}

bool UBngVehicleMeshComponent::UpdateMesh(const FQuat& Rotation, const FVector& Translation, int64 NodesAddress, int32 NodeCount)
{
    if (!Resources.IsValid())
    {
        return false;
    }

    if (NodesAddress)
    {
        if (NodeCount <= 0)
        {
            return false;
        }

        const FVector3f* Nodes = reinterpret_cast<const FVector3f*>(NodesAddress);
        ENQUEUE_RENDER_COMMAND(BngLinkUpdateVehicleMesh)(
            [Resources = Resources, Copy = TArray<FVector3f>(Nodes, NodeCount)](FRHICommandListImmediate& RHICmdList)
            {
                Resources->Skin_RenderThread(RHICmdList, Copy);
            });
    }

    SetRelativeTransform(FTransform(Rotation, Translation));
    return true;
}

FPrimitiveSceneProxy* UBngVehicleMeshComponent::CreateSceneProxy()
{
    return Resources.IsValid() ? new FBngVehicleMeshSceneProxy(this, Resources) : nullptr;
}

FBoxSphereBounds UBngVehicleMeshComponent::CalcBounds(const FTransform& LocalToWorld) const
{
    if (!LocalBounds.IsValid)
    {
        return FBoxSphereBounds(LocalToWorld.GetLocation(), FVector::ZeroVector, 0.0);
    }
    return FBoxSphereBounds(LocalBounds).TransformBy(LocalToWorld);
}

int32 UBngVehicleMeshComponent::GetNumMaterials() const
{
    return MeshSections.Num();
}

void UBngVehicleMeshComponent::OnRegister()
{
    Super::OnRegister();
    if (!Resources.IsValid() && CreateResources())
    {
        SetTime(Time);
    }
}

void UBngVehicleMeshComponent::BeginDestroy()
{
    Resources.Reset();
    Super::BeginDestroy();
}

bool UBngVehicleMeshComponent::CreateResources()
{
    if (MeshPositions.IsEmpty() || MeshPositions.Num() % sizeof(FVector3f) != 0
        || MeshTangents.Num() % sizeof(uint32) != 0 || MeshUVs.Num() % sizeof(FVector4f) != 0
        || MeshIndices.IsEmpty() || MeshSections.IsEmpty())
    {
        return false;
    }

    TArray<FVector3f> Positions = TypedCopy<FVector3f>(MeshPositions);
    TArray<uint32> Tangents = TypedCopy<uint32>(MeshTangents);
    TArray<FVector4f> UVs = TypedCopy<FVector4f>(MeshUVs);
    if (Tangents.Num() != Positions.Num() * 2 || UVs.Num() != Positions.Num())
    {
        return false;
    }

    Resources.Reset();
    const ERHIFeatureLevel::Type FeatureLevel = GetScene() ? GetScene()->GetFeatureLevel() : GMaxRHIFeatureLevel;
    Resources = MakeShareable(
        new FBngVehicleMeshResources(FeatureLevel, MoveTemp(Positions), MoveTemp(Tangents),
            MoveTemp(UVs), TArray<uint32>(MeshIndices), MeshSections),
        [](FBngVehicleMeshResources* Value) { DeleteResourcesOnRenderThread(Value); });

    LocalBounds = PaddedBounds(reinterpret_cast<const FVector3f*>(MeshPositions.GetData()),
        MeshPositions.Num() / sizeof(FVector3f));
    ExpandBoundsWithNodes(LocalBounds, TakeNodes);
    BindStoredSkin();
    UpdateBounds();
    MarkRenderStateDirty();
    return true;
}

void UBngVehicleMeshComponent::BindStoredSkin()
{
    if (!Resources.IsValid() || SkinNodeCount <= 0
        || SkinVertices.Num() != Resources->VertexCount * sizeof(FBngSkinVertex))
    {
        return;
    }

    TArray<FBngSkinVertex> Copy = TypedCopy<FBngSkinVertex>(SkinVertices);
    ENQUEUE_RENDER_COMMAND(BngLinkBindVehicleSkin)(
        [Resources = Resources, Copy = MoveTemp(Copy), NodeCount = SkinNodeCount](FRHICommandListImmediate& RHICmdList) mutable
        {
            Resources->BindSkin_RenderThread(RHICmdList, MoveTemp(Copy), NodeCount);
        });
}

void UBngVehicleMeshComponent::UpdateIndexBuffer()
{
    if (!Resources.IsValid())
    {
        return;
    }

    ENQUEUE_RENDER_COMMAND(BngLinkUpdateVehicleIndices)(
        [Resources = Resources, Indices = TArray<uint32>(MeshIndices)](FRHICommandListImmediate& RHICmdList)
        {
            Resources->UpdateIndices_RenderThread(RHICmdList, Indices);
        });
}

