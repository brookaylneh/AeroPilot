#include "ScenarioLandscapeTools.h"
#if WITH_EDITOR
#include "CollisionQueryParams.h"
#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/World.h"
#include "ImageCore.h"
#include "ImageUtils.h"
#include "Landscape.h"
#include "LandscapeComponent.h"
#include "LandscapeHeightfieldCollisionComponent.h"
#include "LandscapeLayerInfoObject.h"
#include "LandscapeUtils.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#endif

AActor *UScenarioLandscapeTools::CreateLandscape(const FString &WorldFile, const FString &HeightFile,
                                                 int32 ResolutionX, int32 ResolutionY,
                                                 UMaterialInterface *Material,
                                                 const TArray<FString> &LayerNames,
                                                 const FString &LayerInfoFolder)
{
#if WITH_EDITOR
    checkf(GEditor && Material, TEXT("Landscape authoring requires an editor and material"));
    FString Text;
    checkf(FFileHelper::LoadFileToString(Text, *WorldFile), TEXT("Cannot read world configuration"));
    TSharedPtr<FJsonObject> Config;
    checkf(FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Config) && Config.IsValid(),
           TEXT("Invalid world configuration JSON"));
    const auto &Grid = Config->GetObjectField(TEXT("heightfield"));
    checkf(Grid->GetStringField(TEXT("rowAxis")) == TEXT("+Y") &&
               Grid->GetStringField(TEXT("columnAxis")) == TEXT("+X") &&
               Grid->GetStringField(TEXT("encoding")) == TEXT("uint16-little-endian") &&
               Grid->GetIntegerField(TEXT("zeroSample")) == 32768 &&
               Grid->GetIntegerField(TEXT("samplesPerMetre")) == 128,
           TEXT("Unsupported landscape heightfield orientation or encoding"));
    checkf(ResolutionX == Grid->GetIntegerField(TEXT("columns")) &&
               ResolutionY == Grid->GetIntegerField(TEXT("rows")) &&
               ResolutionX > 1 && ResolutionY > 1 &&
               (ResolutionX - 1) % 126 == 0 && (ResolutionY - 1) % 126 == 0,
           TEXT("Landscape resolutions do not match world.json or component topology"));
    const auto &Bounds = Config->GetObjectField(TEXT("searchBoundsCm"));
    const auto &Min = Bounds->GetObjectField(TEXT("min"));
    const auto &Max = Bounds->GetObjectField(TEXT("max"));
    const double MinX = Min->GetNumberField(TEXT("x"));
    const double MinY = Min->GetNumberField(TEXT("y"));
    const double SpanX = Max->GetNumberField(TEXT("x")) - MinX;
    const double SpanY = Max->GetNumberField(TEXT("y")) - MinY;
    const double SpacingCm = Grid->GetNumberField(TEXT("sampleSpacingMetres")) * 100;
    checkf(FMath::IsFinite(SpacingCm) && SpacingCm > 0 &&
               FMath::IsNearlyEqual(SpanX, (ResolutionX - 1) * SpacingCm, 1e-6) &&
               FMath::IsNearlyEqual(SpanY, (ResolutionY - 1) * SpacingCm, 1e-6),
           TEXT("Landscape bounds and sample spacing disagree"));
    TArray<uint8> Bytes;
    checkf(FFileHelper::LoadFileToArray(Bytes, *HeightFile) &&
               Bytes.Num() == int64(ResolutionX) * ResolutionY * 2,
           TEXT("Heightfield byte count does not match world.json"));
    TArray<uint16> Heights;
    Heights.SetNumUninitialized(ResolutionX * ResolutionY);
    for (int32 I = 0; I < Heights.Num(); ++I)
        Heights[I] = Bytes[2 * I] | (uint16(Bytes[2 * I + 1]) << 8);
    UWorld *World = GEditor->GetEditorWorldContext().World();
    checkf(World, TEXT("Editor world is missing"));
    ALandscape *Landscape =
        World->SpawnActor<ALandscape>(FVector(MinX, MinY, 0), FRotator::ZeroRotator);
    checkf(Landscape, TEXT("Cannot spawn landscape"));
    Landscape->SetActorLabel(TEXT("terrain.open-meadow"));
    Landscape->Tags.Add(TEXT("terrain.open-meadow"));
    Landscape->LandscapeMaterial = Material;
    Landscape->SetActorScale3D(FVector(SpacingCm, SpacingCm, 100));
    Landscape->BodyInstance.SetCollisionProfileName(TEXT("ScenarioTerrain"));
    Landscape->CollisionMipLevel = 0;
    Landscape->SimpleCollisionMipLevel = 0;
    TMap<FGuid, TArray<uint16>> HeightLayers;
    HeightLayers.Add(FGuid(), MoveTemp(Heights));
    TMap<FGuid, TArray<FLandscapeImportLayerInfo>> Layers;
    auto &Imports = Layers.Add(FGuid());
    // Masks are uint8 weights in the height.r16 sample layout, written beside it by valley.py.
    for (const FString &Name : LayerNames)
    {
        const FString MaskFile = FPaths::GetPath(HeightFile) / FString::Printf(TEXT("mask-%s.png"), *Name);
        FImage Mask;
        checkf(FImageUtils::LoadImage(*MaskFile, Mask) && Mask.Format == ERawImageFormat::G8 &&
                   Mask.SizeX == ResolutionX && Mask.SizeY == ResolutionY,
               TEXT("Layer mask does not match the heightfield: %s"), *MaskFile);
        const FString AssetName = TEXT("LI_") + Name;
        auto *Info = LoadObject<ULandscapeLayerInfoObject>(
            nullptr, *(LayerInfoFolder / AssetName + TEXT(".") + AssetName), nullptr, LOAD_NoWarn);
        if (!Info)
            Info = UE::Landscape::CreateTargetLayerInfo(FName(Name), LayerInfoFolder, AssetName);
        checkf(Info && Info->GetLayerName() == FName(Name), TEXT("Layer info mismatch for %s"), *Name);
        Info->SetBlendMethod(ELandscapeTargetLayerBlendMethod::FinalWeightBlending, false);
        // Import registers its components before adding target layers, and that registration's
        // FixupWeightmaps deletes every final weightmap whose layer is not yet a target layer.
        Landscape->AddTargetLayer(FName(Name), FLandscapeTargetLayerSettings(Info));
        FLandscapeImportLayerInfo &Import = Imports.Emplace_GetRef(FName(Name));
        Import.LayerInfo = Info;
        Import.SourceFilePath = MaskFile;
        Import.LayerData = TArray<uint8>(Mask.RawData);
    }
    Landscape->Import(FGuid::NewGuid(), 0, 0, ResolutionX - 1, ResolutionY - 1, 2, 63, HeightLayers,
                      *HeightFile, Layers, ELandscapeImportAlphamapType::Additive,
                      TArrayView<const FLandscapeLayer>());
    for (auto Component : Landscape->LandscapeComponents)
    {
        checkf(!Component->GetWeightmapLayerAllocations().IsEmpty(),
               TEXT("Landscape component lost its paint layers on import"));
        Component->SetCollisionProfileName(TEXT("ScenarioTerrain"));
    }
    for (auto Component : Landscape->CollisionComponents)
        Component->SetCollisionProfileName(TEXT("ScenarioTerrain"));
    Landscape->MarkPackageDirty();
    return Landscape;
#else
    return nullptr;
#endif
}

FVector UScenarioLandscapeTools::TraceLandscape(const FVector &Start, const FVector &End)
{
#if WITH_EDITOR
    checkf(GEditor, TEXT("Landscape validation requires an editor"));
    UWorld *World = GEditor->GetEditorWorldContext().World();
    checkf(World && Start.Z > End.Z && Start.X == End.X && Start.Y == End.Y,
           TEXT("Landscape validation requires a vertical trace in an editor world"));
    FHitResult Hit;
    const FCollisionQueryParams Query(SCENE_QUERY_STAT(ScenarioLandscapeValidation), true);
    checkf(World->LineTraceSingleByChannel(Hit, Start, End, ECC_GameTraceChannel1, Query) &&
               Cast<ALandscape>(Hit.GetActor()),
           TEXT("Landscape validation trace missed the landscape at (%f, %f)"), Start.X, Start.Y);
    return Hit.ImpactPoint;
#else
    checkNoEntry();
    return FVector::ZeroVector;
#endif
}
