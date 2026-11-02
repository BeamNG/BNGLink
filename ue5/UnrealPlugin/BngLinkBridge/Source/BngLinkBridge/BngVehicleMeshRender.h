// This Source Code Form is subject to the terms of the bCDDL, v. 1.1.
// If a copy of the bCDDL was not distributed with this
// file, You can obtain one at http://beamng.com/bCDDL-1.1.txt

#pragma once

#include "CoreMinimal.h"
#include "BngVehicleMeshComponent.h"
#include "DynamicMeshBuilder.h"
#include "LocalVertexFactory.h"
#include "MaterialShared.h"
#include "PrimitiveSceneProxy.h"
#include "Rendering/ColorVertexBuffer.h"

struct FBngSkinVertex
{
    uint32 Nodes[4];
    FVector3f Coords;
    FVector3f Residual;
    FVector3f Normal;
    FVector3f Tangent;
    float BinormalSign = 1.0f;
};

static_assert(sizeof(FBngSkinVertex) == 68, "FBngSkinVertex must match the Python and shader layout");

class FBngVertexBuffer final : public FVertexBuffer
{
public:
    FBngVertexBuffer(const TCHAR* InName, EBufferUsageFlags InUsage, EPixelFormat InSrvFormat,
        EPixelFormat InUavFormat = PF_Unknown);

    void SetData(TArray<uint8>&& InData, uint32 InStride);
    void InitRHI(FRHICommandListBase& RHICmdList) override;
    void ReleaseRHI() override;
    FString GetFriendlyName() const override;

    FShaderResourceViewRHIRef SRV;
    FUnorderedAccessViewRHIRef UAV;
    uint32 Size = 0;
    uint32 Stride = 0;

private:
    TArray<uint8> InitialData;
    const TCHAR* Name = nullptr;
    EBufferUsageFlags Usage = BUF_None;
    EPixelFormat SrvFormat = PF_Unknown;
    EPixelFormat UavFormat = PF_Unknown;
};

class FBngVehicleMeshResources
{
public:
    FBngVehicleMeshResources(ERHIFeatureLevel::Type FeatureLevel, TArray<FVector3f>&& Positions,
        TArray<uint32>&& Tangents, TArray<FVector4f>&& UVs, TArray<uint32>&& Indices,
        const TArray<FBngVehicleMeshSection>& InSections);
    ~FBngVehicleMeshResources();

    void BindSkin_RenderThread(FRHICommandListImmediate& RHICmdList, TArray<FBngSkinVertex>&& SkinVertices, int32 NodeCount);
    void Skin_RenderThread(FRHICommandListImmediate& RHICmdList, const TArray<FVector3f>& Nodes);
    void UpdateIndices_RenderThread(FRHICommandListImmediate& RHICmdList, const TArray<uint32>& Indices);

    const int32 VertexCount;
    const TArray<FBngVehicleMeshSection> Sections;
    FBngVertexBuffer PositionBuffer;
    FBngVertexBuffer TangentBuffer;
    FBngVertexBuffer TexCoordBuffer;
    FBufferRHIRef SkinBuffer;
    FShaderResourceViewRHIRef SkinSRV;
    FBufferRHIRef NodeBuffer;
    FShaderResourceViewRHIRef NodeSRV;
    FDynamicMeshIndexBuffer32 IndexBuffer;
    FColorVertexBuffer ColorBuffer;
    FLocalVertexFactory VertexFactory;
    ERHIFeatureLevel::Type FeatureLevel;
    int32 SkinNodeCount = 0;
};

class FBngVehicleMeshSceneProxy final : public FPrimitiveSceneProxy
{
public:
    FBngVehicleMeshSceneProxy(const UBngVehicleMeshComponent* Component,
        const TSharedPtr<FBngVehicleMeshResources, ESPMode::ThreadSafe>& InResources);

    SIZE_T GetTypeHash() const override;
    void GetDynamicMeshElements(const TArray<const FSceneView*>& Views, const FSceneViewFamily& ViewFamily,
        uint32 VisibilityMap, FMeshElementCollector& Collector) const override;
    FPrimitiveViewRelevance GetViewRelevance(const FSceneView* View) const override;
    uint32 GetMemoryFootprint() const override;

private:
    TSharedPtr<FBngVehicleMeshResources, ESPMode::ThreadSafe> Resources;
    TArray<UMaterialInterface*> Materials;
    FMaterialRelevance MaterialRelevance;
};
