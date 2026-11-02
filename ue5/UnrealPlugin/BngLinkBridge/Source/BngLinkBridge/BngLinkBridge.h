// This Source Code Form is subject to the terms of the bCDDL, v. 1.1.
// If a copy of the bCDDL was not distributed with this
// file, You can obtain one at http://beamng.com/bCDDL-1.1.txt

#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "BngLinkBridge.generated.h"

class AActor;
class UActorComponent;
class USceneComponent;
class UTexture2D;

UCLASS()
class UBngLinkBridge : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()

public:
    UFUNCTION(BlueprintCallable, Category = "BngLink")
    static bool RegisterInstanceComponent(AActor* Actor, UActorComponent* Component);

    UFUNCTION(BlueprintCallable, Category = "BngLink")
    static bool UpdateTexture(UTexture2D* Texture, int64 Address, int32 ByteCount);

    // Registers a fixed set of prop components for batched per-frame transform updates
    // Returns a handle for ApplyPropTransforms/UnregisterPropGroup.
    UFUNCTION(BlueprintCallable, Category = "BngLink")
    static int32 RegisterPropGroup(const TArray<USceneComponent*>& Components);

    UFUNCTION(BlueprintCallable, Category = "BngLink")
    static void UnregisterPropGroup(int32 Handle);

    // Applies Count row-major 4x4 double matrices in Unreal space to the registered components
    // (the batched result of transforms.ue_prop_matrices, rotation basis in m[..,0..2], translation in m[..,3]).
    // Returns false for unknown handles.
    UFUNCTION(BlueprintCallable, Category = "BngLink")
    static bool ApplyPropTransforms(int32 Handle, int64 Address, int32 Count);

    // Last measured game thread, render thread and GPU frame times in ms, as shown by stat unit
    UFUNCTION(BlueprintCallable, Category = "BngLink")
    static FVector GetThreadTimes();
};
