// This Source Code Form is subject to the terms of the bCDDL, v. 1.1.
// If a copy of the bCDDL was not distributed with this
// file, You can obtain one at http://beamng.com/bCDDL-1.1.txt

#pragma once

#include "CoreMinimal.h"
#include "GlobalShader.h"
#include "ShaderParameterStruct.h"

class FBngVehicleSkinCS : public FGlobalShader
{
    DECLARE_GLOBAL_SHADER(FBngVehicleSkinCS);
    SHADER_USE_PARAMETER_STRUCT(FBngVehicleSkinCS, FGlobalShader);

public:
    BEGIN_SHADER_PARAMETER_STRUCT(FParameters, )
        SHADER_PARAMETER(uint32, VertexCount)
        SHADER_PARAMETER_SRV(StructuredBuffer<float3>, Nodes)
        SHADER_PARAMETER_SRV(StructuredBuffer<FBngSkinVertex>, SkinVertices)
        SHADER_PARAMETER_UAV(RWBuffer<float>, OutPositions)
        SHADER_PARAMETER_UAV(RWBuffer<uint>, OutTangents)
    END_SHADER_PARAMETER_STRUCT()

    static bool ShouldCompilePermutation(const FGlobalShaderPermutationParameters& Parameters)
    {
        return IsFeatureLevelSupported(Parameters.Platform, ERHIFeatureLevel::SM5);
    }
};
