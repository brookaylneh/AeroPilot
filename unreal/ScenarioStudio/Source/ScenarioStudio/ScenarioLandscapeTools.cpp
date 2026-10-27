#include "ScenarioLandscapeTools.h"
#if WITH_EDITOR
#include "Editor.h"
#include "Landscape.h"
#include "LandscapeComponent.h"
#include "LandscapeHeightfieldCollisionComponent.h"
#include "Misc/FileHelper.h"
#endif

AActor *UScenarioLandscapeTools::CreateLandscape(const FString &HeightFile, int32 Resolution, double WidthCm,
                                                 UMaterialInterface *Material)
{
#if WITH_EDITOR
    if (!GEditor || Resolution != 2017 || !FMath::IsFinite(WidthCm) || WidthCm < 10000 || WidthCm > 1000000 ||
        !Material)
        return nullptr;
    TArray<uint8> Bytes;
    if (!FFileHelper::LoadFileToArray(Bytes, *HeightFile) || Bytes.Num() != Resolution * Resolution * 2)
        return nullptr;
    TArray<uint16> Heights;
    Heights.SetNumUninitialized(Resolution * Resolution);
    for (int32 I = 0; I < Heights.Num(); ++I)
        Heights[I] = Bytes[2 * I] | (uint16(Bytes[2 * I + 1]) << 8);
    UWorld *World = GEditor->GetEditorWorldContext().World();
    if (!World)
        return nullptr;
    ALandscape *Landscape =
        World->SpawnActor<ALandscape>(FVector(-WidthCm / 2, -WidthCm / 2, 0), FRotator::ZeroRotator);
    Landscape->SetActorLabel(TEXT("terrain.open-meadow"));
    Landscape->Tags.Add(TEXT("terrain.open-meadow"));
    Landscape->LandscapeMaterial = Material;
    Landscape->SetActorScale3D(FVector(WidthCm / (Resolution - 1), WidthCm / (Resolution - 1), 100));
    Landscape->BodyInstance.SetCollisionProfileName(TEXT("ScenarioTerrain"));
    Landscape->CollisionMipLevel = 0;
    Landscape->SimpleCollisionMipLevel = 0;
    TMap<FGuid, TArray<uint16>> HeightLayers;
    HeightLayers.Add(FGuid(), MoveTemp(Heights));
    TMap<FGuid, TArray<FLandscapeImportLayerInfo>> Layers;
    Layers.Add(FGuid(), {});
    Landscape->Import(FGuid::NewGuid(), 0, 0, Resolution - 1, Resolution - 1, 2, 63, HeightLayers,
                      *HeightFile, Layers, ELandscapeImportAlphamapType::Additive,
                      TArrayView<const FLandscapeLayer>());
    for (auto Component : Landscape->LandscapeComponents)
        Component->SetCollisionProfileName(TEXT("ScenarioTerrain"));
    for (auto Component : Landscape->CollisionComponents)
        Component->SetCollisionProfileName(TEXT("ScenarioTerrain"));
    Landscape->MarkPackageDirty();
    return Landscape;
#else
    return nullptr;
#endif
}
