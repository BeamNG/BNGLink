// This Source Code Form is subject to the terms of the bCDDL, v. 1.1.
// If a copy of the bCDDL was not distributed with this
// file, You can obtain one at http://beamng.com/bCDDL-1.1.txt

#include "BngLinkBridge.h"

#include "Components/ActorComponent.h"
#include "IImageWrapper.h"
#include "IImageWrapperModule.h"
#include "GameFramework/Actor.h"
#include "Engine/Texture2D.h"
#include "Components/SceneComponent.h"
#include "Modules/ModuleManager.h"
#include "Interfaces/IPluginManager.h"
#include "RenderCore.h"
#include "RHI.h"

namespace
{
    struct FBngPropGroup
    {
        TArray<TWeakObjectPtr<USceneComponent>> Components;
    };

    TMap<int32, FBngPropGroup> GPropGroups;
    int32 GNextPropGroupHandle = 1;
}

class FBngLinkBridgeModule final : public IModuleInterface
{
public:
    void StartupModule() override
    {
        const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("BngLinkBridge"));
        if (Plugin.IsValid())
        {
            AddShaderSourceDirectoryMapping(TEXT("/Plugin/BngLinkBridge"), Plugin->GetBaseDir() / TEXT("Shaders"));
        }
    }
};

bool UBngLinkBridge::RegisterInstanceComponent(AActor* Actor, UActorComponent* Component)
{
    if (!Actor || !Component || Component->GetOwner() != Actor)
    {
        return false;
    }

    Actor->AddInstanceComponent(Component);
    if (!Component->HasBeenCreated())
    {
        Component->OnComponentCreated();
    }
    if (!Component->IsRegistered())
    {
        Component->RegisterComponent();
    }
    return true;
}

bool UBngLinkBridge::UpdateTexture(UTexture2D* Texture, int64 Address, int32 ByteCount)
{
    if (!Texture || !Address || ByteCount <= 0)
    {
        return false;
    }

    IImageWrapperModule& ImageWrapperModule = FModuleManager::LoadModuleChecked<IImageWrapperModule>("ImageWrapper");
    const uint8* CompressedData = reinterpret_cast<const uint8*>(Address);
    const EImageFormat Format = ImageWrapperModule.DetectImageFormat(CompressedData, ByteCount);
    if (Format == EImageFormat::Invalid)
    {
        return false;
    }

    const TSharedPtr<IImageWrapper> ImageWrapper = ImageWrapperModule.CreateImageWrapper(Format);
    if (!ImageWrapper.IsValid() || !ImageWrapper->SetCompressed(CompressedData, ByteCount))
    {
        return false;
    }

    TArray64<uint8> RawData;
    if (!ImageWrapper->GetRaw(ERGBFormat::BGRA, 8, RawData))
    {
        return false;
    }

    FTexturePlatformData* PlatformData = Texture->GetPlatformData();
    const int32 Width = ImageWrapper->GetWidth();
    const int32 Height = ImageWrapper->GetHeight();
    if (PlatformData
        && PlatformData->SizeX == Width
        && PlatformData->SizeY == Height
        && PlatformData->PixelFormat == PF_B8G8R8A8
        && Texture->GetResource())
    {
        uint8* UploadData = static_cast<uint8*>(FMemory::Malloc(RawData.Num()));
        FMemory::Memcpy(UploadData, RawData.GetData(), RawData.Num());
        FUpdateTextureRegion2D* Region = new FUpdateTextureRegion2D(0, 0, 0, 0, Width, Height);
        Texture->UpdateTextureRegions(
            0,
            1,
            Region,
            Width * 4,
            4,
            UploadData,
            [](uint8* Data, const FUpdateTextureRegion2D* Regions)
            {
                FMemory::Free(Data);
                delete Regions;
            }
        );
        return true;
    }

    if (!PlatformData)
    {
        PlatformData = new FTexturePlatformData();
        Texture->SetPlatformData(PlatformData);
    }
    PlatformData->SizeX = Width;
    PlatformData->SizeY = Height;
    PlatformData->PixelFormat = PF_B8G8R8A8;
    PlatformData->Mips.Empty();

    PlatformData->Mips.Add(new FTexture2DMipMap());
    FTexture2DMipMap& Mip = PlatformData->Mips.Last();
    Mip.SizeX = PlatformData->SizeX;
    Mip.SizeY = PlatformData->SizeY;
    Mip.BulkData.Lock(LOCK_READ_WRITE);
    void* TextureData = Mip.BulkData.Realloc(RawData.Num());
    FMemory::Memcpy(TextureData, RawData.GetData(), RawData.Num());
    Mip.BulkData.Unlock();

    Texture->NeverStream = true;
    Texture->UpdateResource();
    return true;
}

int32 UBngLinkBridge::RegisterPropGroup(const TArray<USceneComponent*>& Components)
{
    FBngPropGroup& Group = GPropGroups.FindOrAdd(GNextPropGroupHandle);
    Group.Components.Reset(Components.Num());
    for (USceneComponent* Component : Components)
    {
        Group.Components.Add(Component);
    }
    return GNextPropGroupHandle++;
}

void UBngLinkBridge::UnregisterPropGroup(int32 Handle)
{
    GPropGroups.Remove(Handle);
}

bool UBngLinkBridge::ApplyPropTransforms(int32 Handle, int64 Address, int32 Count)
{
    FBngPropGroup* Group = GPropGroups.Find(Handle);
    if (!Group || !Address)
    {
        return false;
    }

    const double* M = reinterpret_cast<const double*>(Address);
    const int32 Num = FMath::Min(Count, Group->Components.Num());
    for (int32 i = 0; i < Num; ++i, M += 16)
    {
        USceneComponent* Component = Group->Components[i].Get();
        if (!Component)
        {
            continue;
        }

        // Input matrices transform column vectors, FMatrix row vectors:
        // the upper 3x3 is transposed and the translation is read from the last column
        const FMatrix Basis(
            FPlane(M[0], M[4], M[8], 0.0),
            FPlane(M[1], M[5], M[9], 0.0),
            FPlane(M[2], M[6], M[10], 0.0),
            FPlane(0.0, 0.0, 0.0, 1.0));

        Component->SetRelativeTransform(FTransform(
            FQuat(Basis.Rotator()),
            FVector(M[3], M[7], M[11]),
            FVector::OneVector));
        Component->UpdateComponentToWorld();
    }
    return true;
}

FVector UBngLinkBridge::GetThreadTimes()
{
    return FVector(
        FPlatformTime::ToMilliseconds(GGameThreadTime),
        FPlatformTime::ToMilliseconds(GRenderThreadTime),
        FPlatformTime::ToMilliseconds(RHIGetGPUFrameCycles()));
}

IMPLEMENT_MODULE(FBngLinkBridgeModule, BngLinkBridge)
