#include "ScenarioManager.h"
#include "Engine/World.h"
#include "CollisionQueryParams.h"
#include "CollisionShape.h"
#include "Components/PrimitiveComponent.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/Pawn.h"
#include "Kismet/GameplayStatics.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Dom/JsonObject.h"

AScenarioManager::AScenarioManager()
{
    PrimaryActorTick.bCanEverTick = true;
    Tags.Add(TEXT("ScenarioManager"));
}

bool AScenarioManager::SelectRegion(FName Id, FString &Error)
{
    if (!Catalog ||
        !Catalog->Regions.ContainsByPredicate([&](const FScenarioRegion &R) { return R.Id == Id; }))
    {
        Error = TEXT("Unknown region");
        return false;
    }
    RegionId = Id;
    bPlaced = false;
    if (Person)
        Person->SetActorHiddenInGame(true);
    return true;
}

bool AScenarioManager::ValidateAppearance(const FScenarioAppearance &Selection,
                                          const FScenarioPersonPreset *&Preset, FString &Error) const
{
    if (!Catalog)
    {
        Error = TEXT("Authored catalog is missing");
        return false;
    }
    const FName *PersonId = Selection.Options.Find(TEXT("personId"));
    Preset = PersonId ? Catalog->People.FindByPredicate(
                            [&](const FScenarioPersonPreset &P) { return P.Id == *PersonId; })
                      : nullptr;
    if (!Preset || Preset->ActorClass.IsNull())
    {
        Error = TEXT("Character asset is not registered");
        return false;
    }
    if (Selection.Options.FindRef(TEXT("topId")) != Preset->TopId ||
        Selection.Options.FindRef(TEXT("bottomId")) != Preset->BottomId ||
        (!Preset->bSupportsBackpack && Selection.Options.FindRef(TEXT("backpackId")) != TEXT("none")))
    {
        Error = TEXT("The selected equipment does not fit this character");
        return false;
    }
    if (Selection.Options.Num() != Catalog->AppearanceOptions.Num() + 1)
    {
        Error = TEXT("Incomplete or unknown appearance fields");
        return false;
    }
    for (const auto &Group : Catalog->AppearanceOptions)
    {
        const FName *Value = Selection.Options.Find(Group.Key);
        if (!Value || !Group.AllowedValues.Contains(*Value))
        {
            Error = FString::Printf(TEXT("Unsupported option: %s"), *Group.Key.ToString());
            return false;
        }
    }
    return true;
}

bool AScenarioManager::SetAppearance(const FScenarioAppearance &Selection, FString &Error)
{
    const FScenarioPersonPreset *Preset = nullptr;
    if (!ValidateAppearance(Selection, Preset, Error))
        return false;
    const bool ChangingPerson = Appearance.Options.FindRef(TEXT("personId")) != Preset->Id;
    const FName NextSituation = ChangingPerson ? FName(TEXT("lost")) : SituationId;
    if (!Preset->SupportedSituations.Contains(NextSituation))
    {
        Error = TEXT("This character does not support the selected situation");
        return false;
    }
    FVector Ground = Person ? Person->GetActorLocation() : FVector::ZeroVector;
    if (bPlaced && !ValidateGround(RegionId, Ground, *Preset, NextSituation, Ground, Error))
        return false;
    UClass *Class = Preset->ActorClass.LoadSynchronous();
    if (!Class)
    {
        Error = TEXT("Character Blueprint missing from cooked build");
        return false;
    }
    // Updating tint/equipment on an existing preset preserves the skeletal
    // actor, animation and stable identity instead of respawning it.
    if (Person && Person->GetClass() == Class &&
        Appearance.Options.FindRef(TEXT("personId")) == Selection.Options.FindRef(TEXT("personId")))
    {
        if (!Person->ApplyAppearance(Selection))
        {
            Person->ApplyAppearance(Appearance);
            Error = TEXT("Character could not apply the selected wardrobe");
            return false;
        }
        Appearance = Selection;
        return true;
    }
    FActorSpawnParameters Params;
    Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
    AScenarioPerson *Candidate =
        GetWorld()->SpawnActor<AScenarioPerson>(Class, Ground, FRotator(0, Yaw, 0), Params);
    if (!Candidate || !Candidate->ApplyAppearance(Selection) || !Candidate->ApplySituation(NextSituation))
    {
        if (Candidate)
            Candidate->Destroy();
        Error = TEXT("Character could not apply the selected wardrobe");
        return false;
    }
    Candidate->Tags.AddUnique(TEXT("scenario.target.primary"));
    Candidate->SetActorHiddenInGame(!bPlaced);
    if (Person)
        Person->Destroy();
    Person = Candidate;
    Appearance = Selection;
    SituationId = NextSituation;
    ResetBehavior();
    return true;
}

bool AScenarioManager::SetSituation(FName Id, FString &Error)
{
    const FScenarioPersonPreset *Preset = nullptr;
    if (!ValidateAppearance(Appearance, Preset, Error))
        return false;
    if (!Preset->SupportedSituations.Contains(Id) || !Person)
    {
        Error = TEXT("This character does not support the selected situation");
        return false;
    }
    if (bPlaced)
    {
        FVector Ground;
        if (!ValidateGround(RegionId, Person->GetActorLocation(), *Preset, Id, Ground, Error))
            return false;
    }
    if (!Person->ApplySituation(Id))
    {
        Error = TEXT("The selected situation animation is unavailable");
        return false;
    }
    SituationId = Id;
    ResetBehavior();
    return true;
}

bool AScenarioManager::PreviewPerson(FString &Error)
{
    if (!Person)
    {
        Error = TEXT("No configured character is available");
        return false;
    }
    if (!bPlaced)
    {
        const auto *Region =
            Catalog->Regions.FindByPredicate([this](const FScenarioRegion &R) { return R.Id == RegionId; });
        const FScenarioPersonPreset *Preset = nullptr;
        FVector Ground;
        if (!Region || !ValidateAppearance(Appearance, Preset, Error) ||
            !ValidateGround(RegionId, Region->PreviewTargetCm, *Preset, SituationId, Ground, Error))
            return false;
        Person->SetActorLocation(Ground);
    }
    Person->SetActorHiddenInGame(false);
    return true;
}

bool AScenarioManager::ValidateGround(FName Region, const FVector &Point, const FScenarioPersonPreset &Preset,
                                      FName Situation, FVector &Ground, FString &Error) const
{
    if (!Catalog || Point.ContainsNaN())
    {
        Error = TEXT("Invalid world coordinate");
        return false;
    }
    const FScenarioRegion *R =
        Catalog->Regions.FindByPredicate([&](const FScenarioRegion &Entry) { return Entry.Id == Region; });
    const bool Resting = Situation == TEXT("injured");
    const double BodyScale = Preset.HeightCm / 170.0;
    const double Margin = Resting ? 100 * BodyScale : Preset.CapsuleRadiusCm;
    if (!R || Point.X < R->Min.X + Margin || Point.X > R->Max.X - Margin || Point.Y < R->Min.Y + Margin ||
        Point.Y > R->Max.Y - Margin)
    {
        Error = TEXT("Outside search region");
        return false;
    }
    FCollisionQueryParams Query(SCENE_QUERY_STAT(ScenarioGround), true);
    if (Person)
    {
        Query.AddIgnoredActor(Person);
        TArray<AActor *> Children;
        Person->GetAllChildActors(Children, true);
        Query.AddIgnoredActors(Children);
    }
    if (auto *PC = UGameplayStatics::GetPlayerController(this, 0))
        Query.AddIgnoredActor(PC->GetPawn());
    FHitResult Hit;
    if (!GetWorld()->SweepSingleByChannel(Hit, FVector(Point.X, Point.Y, R->Max.Z),
                                          FVector(Point.X, Point.Y, R->Min.Z), FQuat::Identity,
                                          ECC_GameTraceChannel1,
                                          FCollisionShape::MakeSphere(TerrainProbeRadiusCm), Query))
    {
        Error = TEXT("No valid terrain at this location");
        return false;
    }
    const double Slope = FMath::RadiansToDegrees(FMath::Acos(FMath::Clamp(Hit.ImpactNormal.Z, -1.0, 1.0)));
    if (Slope > (Resting ? 8.0 : R->MaximumSlopeDegrees))
    {
        Error = TEXT("This ground is too steep for the selected situation");
        return false;
    }
    // Ground support on all sides prevents placement straddling a cliff or a deep hole.
    const double Support = Resting ? 90 * BodyScale : 20;
    for (const FVector &Offset :
         {FVector(Support, 0, 0), FVector(-Support, 0, 0), FVector(0, Support, 0), FVector(0, -Support, 0)})
    {
        FHitResult Foot;
        if (!GetWorld()->LineTraceSingleByChannel(Foot, Hit.ImpactPoint + Offset + FVector(0, 0, 60),
                                                  Hit.ImpactPoint + Offset - FVector(0, 0, 60),
                                                  ECC_GameTraceChannel1, Query) ||
            FMath::Abs(Foot.ImpactPoint.Z - Hit.ImpactPoint.Z) > (Resting ? 8 : 12))
        {
            Error = TEXT("The selected pose needs more even ground");
            return false;
        }
    }
    Query.AddIgnoredActor(Hit.GetActor());
    FCollisionObjectQueryParams Objects;
    Objects.AddObjectTypesToQuery(ECC_WorldStatic);
    Objects.AddObjectTypesToQuery(ECC_WorldDynamic);
    const FVector Center = Hit.ImpactPoint + FVector(0, 0, Preset.CapsuleHalfHeightCm + 2);
    if (GetWorld()->OverlapAnyTestByObjectType(
            Center, FQuat::Identity, Objects,
            FCollisionShape::MakeCapsule(Resting ? 90 * BodyScale : Preset.CapsuleRadiusCm,
                                         Preset.CapsuleHalfHeightCm),
            Query))
    {
        Error = TEXT("A solid obstacle intersects the person");
        return false;
    }
    Ground = Hit.ImpactPoint;
    return true;
}

bool AScenarioManager::PlaceAtGround(double X, double Y, FString &Error)
{
    const FScenarioPersonPreset *Preset = nullptr;
    if (!Person || !ValidateAppearance(Appearance, Preset, Error))
    {
        Error = TEXT("Select a configured person first");
        return false;
    }
    if (!FMath::IsFinite(X) || !FMath::IsFinite(Y))
    {
        Error = TEXT("Invalid coordinates");
        return false;
    }
    FVector Ground;
    if (!ValidateGround(RegionId, FVector(X, Y, 0), *Preset, SituationId, Ground, Error))
        return false;
    Person->SetActorLocationAndRotation(Ground, FRotator(0, Yaw, 0), false, nullptr,
                                        ETeleportType::TeleportPhysics);
    Person->SetActorHiddenInGame(false);
    bPlaced = true;
    ResetBehavior();
    return true;
}

bool AScenarioManager::GroundFromScreen(float U, float V, FVector &Ground, FString &Error) const
{
    if (!FMath::IsFinite(U) || !FMath::IsFinite(V) || U < 0 || U > 1 || V < 0 || V > 1)
    {
        Error = TEXT("Invalid normalized screen coordinates");
        return false;
    }
    APlayerController *PC = UGameplayStatics::GetPlayerController(this, 0);
    int32 Width = 0, Height = 0;
    FVector Origin, Direction;
    if (!PC)
    {
        Error = TEXT("No camera controller");
        return false;
    }
    PC->GetViewportSize(Width, Height);
    if (!PC->DeprojectScreenPositionToWorld(U * Width, V * Height, Origin, Direction))
    {
        Error = TEXT("Cannot deproject camera position");
        return false;
    }
    FHitResult Hit;
    FCollisionQueryParams Query;
    if (Person)
    {
        Query.AddIgnoredActor(Person);
        TArray<AActor *> Children;
        Person->GetAllChildActors(Children, true);
        Query.AddIgnoredActors(Children);
    }
    // Visibility trace prevents clicking through a rock to the terrain behind it.
    const auto *Region =
        Catalog
            ? Catalog->Regions.FindByPredicate([this](const FScenarioRegion &R) { return R.Id == RegionId; })
            : nullptr;
    if (!Region)
    {
        Error = TEXT("Unknown search area");
        return false;
    }
    const double Range = FMath::Max(100000., FVector::Dist(Region->Min, Region->Max) * 4);
    if (!GetWorld()->LineTraceSingleByChannel(Hit, Origin, Origin + Direction * Range, ECC_Visibility,
                                              Query) ||
        !Hit.Component.IsValid() ||
        Hit.Component->GetCollisionResponseToChannel(ECC_GameTraceChannel1) != ECR_Block)
    {
        Error = TEXT("Select visible ground");
        return false;
    }
    if (Hit.ImpactPoint.X < Region->Min.X || Hit.ImpactPoint.X > Region->Max.X ||
        Hit.ImpactPoint.Y < Region->Min.Y || Hit.ImpactPoint.Y > Region->Max.Y)
    {
        Error = TEXT("Outside search region");
        return false;
    }
    Ground = Hit.ImpactPoint;
    return true;
}

bool AScenarioManager::SetYaw(double Degrees, FString &Error)
{
    if (!FMath::IsFinite(Degrees) || Degrees < 0 || Degrees > 359)
    {
        Error = TEXT("Yaw must be between 0 and 359 degrees");
        return false;
    }
    Yaw = Degrees;
    if (Person)
    {
        Person->SetActorRotation(FRotator(0, Yaw, 0));
        Behavior.Destination = Person->GetActorLocation();
        Behavior.bWalking = false;
        Behavior.AnimationTime = 0;
        Behavior.PauseSeconds = 2;
        verify(Person->ApplyMotion(false, 0));
    }
    return true;
}

FVector AScenarioManager::ToCosysNed(const FVector &UnrealCm) const
{
    const FVector Relative = (UnrealCm - (Catalog ? Catalog->CosysOriginCm : FVector::ZeroVector)) / 100;
    return FVector(Relative.X, Relative.Y, -Relative.Z);
}

FString AScenarioManager::ExportAuthoringJson() const
{
    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("regionId"), RegionId.ToString().ToLower());
    Root->SetStringField(TEXT("situationId"), SituationId.ToString().ToLower());
    TSharedRef<FJsonObject> Options = MakeShared<FJsonObject>();
    // FName display casing can differ between editor and packaged name tables.
    // Send canonical identifiers, independent of asset load order.
    for (const auto &Option : Appearance.Options)
        Options->SetStringField(FScenarioAppearance::JsonKey(Option.Key), Option.Value.ToString().ToLower());
    Root->SetObjectField(TEXT("appearance"), Options);
    if (bPlaced && Person)
    {
        const FVector P = Person->GetActorLocation();
        TSharedRef<FJsonObject> Position = MakeShared<FJsonObject>();
        Position->SetNumberField(TEXT("x"), P.X);
        Position->SetNumberField(TEXT("y"), P.Y);
        Position->SetNumberField(TEXT("z"), P.Z);
        Root->SetObjectField(TEXT("position"), Position);
        Root->SetObjectField(TEXT("behavior"), BehaviorJson());
    }
    else
        Root->SetField(TEXT("position"), MakeShared<FJsonValueNull>());
    TSharedRef<FJsonObject> Rotation = MakeShared<FJsonObject>();
    Rotation->SetNumberField(TEXT("pitch"), 0);
    Rotation->SetNumberField(TEXT("yaw"),
                             Person ? FMath::Fmod(Person->GetActorRotation().Yaw + 360., 360.) : Yaw);
    Rotation->SetNumberField(TEXT("roll"), 0);
    Root->SetObjectField(TEXT("rotation"), Rotation);
    FString Json;
    FJsonSerializer::Serialize(Root, TJsonWriterFactory<>::Create(&Json));
    return Json;
}
