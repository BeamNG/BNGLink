// This Source Code Form is subject to the terms of the bCDDL, v. 1.1.
// If a copy of the bCDDL was not distributed with this
// file, You can obtain one at http://beamng.com/bCDDL-1.1.txt

#pragma once

#include "CoreMinimal.h"
#include "Components/MeshComponent.h"
#include "BngVehicleMeshComponent.generated.h"

class FBngVehicleMeshResources;

// Index range drawn with the material slot of the same index as the section
USTRUCT(BlueprintType)
struct FBngVehicleMeshSection
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadWrite, Category = "BngLink")
    int32 FirstIndex = 0;

    UPROPERTY(BlueprintReadWrite, Category = "BngLink")
    int32 NumTriangles = 0;

    UPROPERTY(BlueprintReadWrite, Category = "BngLink")
    int32 MinVertexIndex = 0;

    UPROPERTY(BlueprintReadWrite, Category = "BngLink")
    int32 MaxVertexIndex = 0;
};

// Mesh whose positions, normals and tangents are skinned on the GPU from per-frame node positions.
UCLASS(BlueprintType, ClassGroup = Rendering)
class UBngVehicleMeshComponent : public UMeshComponent
{
    GENERATED_BODY()

public:
    UBngVehicleMeshComponent();

    // Addresses point to packed float32 arrays. Indices are uint32.
    UFUNCTION(BlueprintCallable, Category = "BngLink")
    bool InitMesh(int32 VertexCount, int64 PositionsAddress, int64 NormalsAddress, int64 TangentsAddress,
        int64 UVsAddress, int32 IndexCount, int64 IndicesAddress, const TArray<FBngVehicleMeshSection>& Sections);

    UFUNCTION(BlueprintCallable, Category = "BngLink")
    bool UpdateIndices(int32 IndexCount, int64 IndicesAddress);

    // SkinVerticesAddress points to records matching gpu_skin.SKIN_VERTEX_DTYPE.
    UFUNCTION(BlueprintCallable, Category = "BngLink")
    bool BindSkin(int32 NodeCount, int64 SkinVerticesAddress, int32 VertexCount);

    // Samples are packed as time float64, nodes float32 xyz, rotation float32 xyzw and translation float32 xyz.
    UFUNCTION(BlueprintCallable, Category = "BngLink")
    bool SetTake(double Duration, int32 SampleCount, int32 NodeCount, int64 TimesAddress,
        int64 NodesAddress, int64 RotationsAddress, int64 TranslationsAddress);

    UFUNCTION(BlueprintCallable, Category = "BngLink")
    bool SetTakeIndices(int32 SampleCount, int32 IndexCount, int64 TimesAddress, int64 IndicesAddress);

    UPROPERTY(EditAnywhere, Interp, BlueprintSetter = SetTime, Category = "BngLink")
    double Time = 0.0;

    UFUNCTION(BlueprintSetter, Category = "BngLink")
    void SetTime(double InTime);

    // Sets the relative transform and uploads float32 node positions, unless NodesAddress is 0.
    UFUNCTION(BlueprintCallable, Category = "BngLink")
    bool UpdateMesh(const FQuat& Rotation, const FVector& Translation, int64 NodesAddress, int32 NodeCount);

    FPrimitiveSceneProxy* CreateSceneProxy() override;
    FBoxSphereBounds CalcBounds(const FTransform& LocalToWorld) const override;
    int32 GetNumMaterials() const override;
    void OnRegister() override;
    void BeginDestroy() override;

private:
    bool CreateResources();
    void BindStoredSkin();
    void UpdateIndexBuffer();

    UPROPERTY()
    TArray<uint8> MeshPositions;

    UPROPERTY()
    TArray<uint8> MeshTangents;

    UPROPERTY()
    TArray<uint8> MeshUVs;

    UPROPERTY()
    TArray<uint32> MeshIndices;

    UPROPERTY()
    TArray<FBngVehicleMeshSection> MeshSections;

    UPROPERTY()
    TArray<uint8> SkinVertices;

    UPROPERTY()
    int32 SkinNodeCount = 0;

    UPROPERTY()
    double TakeDuration = 0.0;

    UPROPERTY()
    int32 TakeNodeCount = 0;

    UPROPERTY()
    TArray<double> TakeTimes;

    UPROPERTY()
    TArray<float> TakeNodes;

    UPROPERTY()
    TArray<float> TakeRotations;

    UPROPERTY()
    TArray<float> TakeTranslations;

    UPROPERTY()
    TArray<double> TakeIndexTimes;

    UPROPERTY()
    TArray<uint32> TakeIndices;

    TSharedPtr<FBngVehicleMeshResources, ESPMode::ThreadSafe> Resources;
    TArray<FVector3f> InterpolatedNodes;
    int32 TakeIndexSample = -1;
    FBox LocalBounds = FBox(ForceInit);
};
