#include "ScenarioSession.h"
#include "Components/RectLightComponent.h"
#include "ScenarioManager.h"
#include "Camera/CameraActor.h"
#include "RuntimeGen/GenSources/PCGGenSourceComponent.h"
#include "Camera/CameraComponent.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "IPixelStreaming2Module.h"
#include "IPixelStreaming2Streamer.h"
#include "IPixelStreaming2InputHandler.h"
#include "PixelStreaming2Delegates.h"
#include "SimMode/SimModeBase.h"
#include "PIPCamera.h"
#include "Serialization/MemoryReader.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Dom/JsonObject.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "HAL/FileManager.h"
#include "TimerManager.h"
#include "Components/DecalComponent.h"
#include "Components/SceneComponent.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "UnrealClient.h"
#include "Engine/GameViewportClient.h"
#include "ScenarioVideoProducer.h"
#include "DroneFoundation.h"

namespace
{
TSharedPtr<FJsonObject> Parse(const FString &Text)
{
    TSharedPtr<FJsonObject> Object;
    FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Object);
    return Object;
}
FString Stringify(const TSharedRef<FJsonObject> &Object)
{
    FString Text;
    FJsonSerializer::Serialize(Object, TJsonWriterFactory<>::Create(&Text));
    return Text;
}
bool Fields(const TSharedPtr<FJsonObject> &Object, std::initializer_list<const TCHAR *> Names)
{
    if (!Object || Object->Values.Num() != static_cast<int32>(Names.size()))
        return false;
    for (const TCHAR *Name : Names)
        if (!Object->HasField(Name))
            return false;
    return true;
}
FString Directory()
{
    FString Override;
    if (FParse::Value(FCommandLine::Get(), TEXT("ScenarioSaveDir="), Override) && !Override.IsEmpty())
        return FPaths::ConvertRelativePathToFull(Override);
    return FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("AuthoringScenarios"));
}
bool ValidId(const FString &Id)
{
    FGuid Guid;
    return FGuid::ParseExact(Id, EGuidFormats::DigitsWithHyphens, Guid);
}
}

AScenarioSession::AScenarioSession()
{
    PrimaryActorTick.bCanEverTick = true;
    Tags.Add(TEXT("scenario.session"));
    RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("AuthoringRoot"));
    Marker = CreateDefaultSubobject<UDecalComponent>(TEXT("PlacementMarker"));
    Marker->SetupAttachment(RootComponent);
    Marker->DecalSize = FVector(100, 65, 65);
    Marker->SetRelativeRotation(FRotator(-90, 0, 0));
    Marker->SetVisibility(false);
}
void AScenarioSession::PostInitializeComponents()
{
    Super::PostInitializeComponents();
    if (GetWorld()->IsGameWorld())
        check(GetWorld()->SpawnActor<ADroneFoundation>());
}
void AScenarioSession::BeginPlay()
{
    Super::BeginPlay();
    auto *Delegates = UPixelStreaming2Delegates::Get();
    check(Delegates);
    PlayerDisconnectedHandle = Delegates->OnClosedConnectionNative.AddUObject(
        this, &AScenarioSession::PlayerDisconnected);
    if (!Manager)
    {
        TActorIterator<AScenarioManager> It(GetWorld());
        if (It)
            Manager = *It;
    }
    GetWorldTimerManager().SetTimer(BindTimer, this, &AScenarioSession::BindStream, 1.0f, true);
}
void AScenarioSession::EndPlay(const EEndPlayReason::Type Reason)
{
    GetWorldTimerManager().ClearTimer(BindTimer);
    if (auto *Delegates = UPixelStreaming2Delegates::Get())
        Delegates->OnClosedConnectionNative.Remove(PlayerDisconnectedHandle);
    BoundStreamer.Reset();
    Super::EndPlay(Reason);
}

void AScenarioSession::PlayerDisconnected(FString StreamerId, FString PlayerId)
{
    check(IsInGameThread());
    auto Streamer = BoundStreamer.Pin();
    if (Streamer && Streamer->GetId() == StreamerId && PlayerId == DroneViewPlayer)
    {
        DroneViewPlayer.Empty();
        if (bMissionActive)
            GetWorld()->GetGameViewport()->bDisableWorldRendering = true;
    }
}
void AScenarioSession::BindStream()
{
    if (!Manager || !Manager->Catalog)
        return;
    if (!bInitialized)
    {
        if (!Camera)
        {
            Camera = GetWorld()->SpawnActor<ACameraActor>();
            GrassSource = NewObject<UPCGGenSourceComponent>(Camera, TEXT("StudioGrassSource"));
            GrassSource->RegisterComponent();
            Camera->GetCameraComponent()->SetFieldOfView(DesiredFieldOfView);
            PreviewFill = NewObject<URectLightComponent>(Camera, TEXT("CharacterPreviewFill"));
            PreviewFill->SetupAttachment(Camera->GetRootComponent());
            PreviewFill->SetMobility(EComponentMobility::Movable);
            PreviewFill->SetRelativeLocation(FVector(0, -90, 60));
            PreviewFill->SetRelativeRotation(FRotator(-10, 15, 0));
            PreviewFill->SetIntensityUnits(ELightUnits::Lumens);
            PreviewFill->SetIntensity(200000);
            PreviewFill->SetAttenuationRadius(1000);
            PreviewFill->SetSourceWidth(200);
            PreviewFill->SetSourceHeight(200);
            PreviewFill->SetCastShadows(false);
            PreviewFill->RegisterComponent();
        }
        FString Error;
        if (!ResetScenario(Error))
        {
            UE_LOG(LogTemp, Fatal, TEXT("Scenario initialization: %s"), *Error);
            return;
        }
        bInitialized = true;
    }
    auto &Module = IPixelStreaming2Module::Get();
    if (!Module.IsReady())
        return;
    Module.ForEachStreamer([this](TSharedPtr<IPixelStreaming2Streamer> Streamer) {
        if (BoundStreamer.Pin() == Streamer)
            return;
        if (!GEngine || !GEngine->GameViewport)
            return;
        auto Video = FScenarioVideoProducer::Create(GEngine->GameViewport->GetWindow());
        if (!Video)
            return;
        Streamer->SetVideoProducer(Video);
        auto Input = Streamer->GetInputHandler().Pin();
        if (!Input)
            return;
        TWeakObjectPtr<AScenarioSession> WeakThis(this);
        Input->SetElevatedCheck([](FString) { return false; });
        // Built-in engine command messages are not part of the authoring protocol.
        Input->RegisterMessageHandler(TEXT("Command"), [](FString, FMemoryReader) {});
        for (const TCHAR *Name : {TEXT("KeyDown"), TEXT("KeyUp"), TEXT("KeyPress"), TEXT("TextboxEntry")})
        {
            Input->RegisterMessageHandler(Name, [](FString, FMemoryReader) {});
        }
        Input->RegisterMessageHandler(
            TEXT("UIInteraction"), [WeakThis](FString Player, FMemoryReader Reader) {
                if (Reader.TotalSize() < 2)
                    return;
                uint16 Length = 0;
                Reader << Length;
                if (Length > 60000 || Reader.TotalSize() != 2 + Length * sizeof(TCHAR))
                    return;
                FString Message;
                Message.GetCharArray().SetNumUninitialized(Length + 1);
                Reader.Serialize(Message.GetCharArray().GetData(), Length * sizeof(TCHAR));
                Message.GetCharArray()[Length] = 0;
                check(IsInGameThread());
                if (WeakThis.IsValid())
                    WeakThis->HandleCommand(Player, Message);
            });
        BoundStreamer = Streamer;
        if (FParse::Param(FCommandLine::Get(), TEXT("ScenarioDiagnostics")))
        {
            if (auto Producer = Streamer->GetVideoProducer().Pin())
            {
                auto Count = MakeShared<int32>(0);
                Producer->OnFramePushed.AddLambda([Count](const IPixelCaptureInputFrame &Frame) {
                    if ((*Count)++ < 5)
                        UE_LOG(LogTemp, Display, TEXT("Scenario capture frame: %d x %d format %d"),
                               Frame.GetWidth(), Frame.GetHeight(), Frame.GetType());
                });
            }
            FTimerHandle ScreenshotTimer;
            GetWorldTimerManager().SetTimer(
                ScreenshotTimer,
                []() {
                    FScreenshotRequest::RequestScreenshot(
                        FPaths::ProjectSavedDir() / TEXT("Screenshots/authoring-runtime.png"), false, false);
                },
                8.0f, false);
        }
        UE_LOG(LogTemp, Display, TEXT("Scenario authoring protocol attached to %s"), *Streamer->GetId());
    });
}

bool AScenarioSession::ResetScenario(FString &Error)
{
    const auto *Catalog = Manager->Catalog.Get();
    if (Catalog->Regions.IsEmpty() || Catalog->People.IsEmpty())
    {
        Error = TEXT("Scenario catalog is empty");
        return false;
    }
    if (!Manager->SelectRegion(Catalog->Regions[0].Id, Error))
        return false;
    FScenarioAppearance Appearance;
    Appearance.Options.Add(TEXT("personId"), Catalog->People[0].Id);
    for (const auto &Group : Catalog->AppearanceOptions)
    {
        if (Group.AllowedValues.IsEmpty())
        {
            Error = TEXT("An appearance option has no values");
            return false;
        }
        Appearance.Options.Add(Group.Key, Group.AllowedValues[0]);
    }
    Appearance.Options[TEXT("topId")] = Catalog->People[0].TopId;
    Appearance.Options[TEXT("bottomId")] = Catalog->People[0].BottomId;
    if (!Manager->SetAppearance(Appearance, Error) || !Manager->SetYaw(0, Error))
        return false;
    LastSavedId.Empty();
    Briefing = MakeShared<FJsonObject>();
    for (const TCHAR *Key :
         {TEXT("brief"), TEXT("portrait")})
        Briefing->SetStringField(Key, TEXT(""));
    return Manager->SetSituation(TEXT("lost"), Error) && SetCreatorStage(TEXT("person"), Error);
}

bool AScenarioSession::SetCreatorStage(const FString &Stage, FString &Error)
{
    if (Stage == TEXT("person") || Stage == TEXT("situation"))
    {
        if (!Manager->PreviewPerson(Error) || !SetCamera(TEXT("creator"), Error))
            return false;
    }
    else if (Stage == TEXT("placement"))
    {
        // Keep the preview pose until the location chooser starts the flight.
        StopNavigation();
        CameraTransitionDuration = 0;
        bNavigationFlight = bBriefingRisePending = false;
        Manager->Person->SetActorHiddenInGame(!Manager->bPlaced);
    }
    else if (Stage == TEXT("briefing"))
    {
        const bool Departing =
            Stage == TEXT("briefing") && CreatorStage == TEXT("placement") && Manager->bPlaced;
        if (!(Departing ? TransitionCamera(TEXT("person"), .9, Error) : SetCamera(TEXT("start"), Error)))
            return false;
        bBriefingRisePending = Departing;
        Manager->Person->SetActorHiddenInGame(!Manager->bPlaced);
    }
    else
    {
        Error = TEXT("Unknown creator stage");
        return false;
    }
    CreatorStage = Stage;
    Manager->SetBehaviorActive(Stage == TEXT("placement") || Stage == TEXT("briefing"));
    bShowMarker = false;
    UpdateMarker();
    return true;
}

bool AScenarioSession::SetBriefing(const TSharedPtr<FJsonObject> &Value, FString &Error)
{
    if (!Fields(Value, {TEXT("brief"), TEXT("portrait")}))
    {
        Error = TEXT("Unknown or missing briefing fields");
        return false;
    }
    for (const auto &Field : Value->Values)
    {
        FString Text;
        const bool IsPortrait = Field.Key == TEXT("portrait");
        if (!Field.Value->TryGetString(Text) || Text.Len() > (IsPortrait ? 45000 : 4000) ||
            (IsPortrait && !Text.IsEmpty() && !Text.StartsWith(TEXT("data:image/jpeg;base64,"))))
        {
            Error = TEXT("Invalid briefing text or portrait");
            return false;
        }
    }
    Briefing = Value;
    return true;
}

TSharedRef<FJsonObject> AScenarioSession::PublicBriefing() const
{
    // Construct the rescue-facing record from an allowlist. Never serialize Manager authoring here.
    auto Result = MakeShared<FJsonObject>();
    Result->SetNumberField(TEXT("schemaVersion"), 2);
    Result->SetObjectField(TEXT("report"), Briefing.ToSharedRef());
    auto World = MakeShared<FJsonObject>();
    World->SetStringField(TEXT("environmentId"), Manager->Catalog->EnvironmentId.ToString().ToLower());
    World->SetStringField(TEXT("assetVersion"), Manager->Catalog->AssetVersion);
    World->SetStringField(TEXT("coordinateSystem"), TEXT("UE_LH_Z_UP_CENTIMETERS"));
    TArray<TSharedPtr<FJsonValue>> Bounds;
    for (const auto &Region : Manager->Catalog->Regions)
    {
        auto Item = MakeShared<FJsonObject>();
        Item->SetStringField(TEXT("id"), Region.Id.ToString().ToLower());
        Item->SetNumberField(TEXT("minX"), Region.Min.X);
        Item->SetNumberField(TEXT("maxX"), Region.Max.X);
        Item->SetNumberField(TEXT("minY"), Region.Min.Y);
        Item->SetNumberField(TEXT("maxY"), Region.Max.Y);
        Bounds.Add(MakeShared<FJsonValueObject>(Item));
    }
    World->SetArrayField(TEXT("searchBounds"), Bounds);
    Result->SetObjectField(TEXT("world"), World);
    return Result;
}

void AScenarioSession::DisableGrassSource()
{
    check(GrassSource);
    bMissionActive = true;
    StopNavigation();
    NavigationPlayer.Empty();
    SetActorTickEnabled(false);
    GrassSource->DestroyComponent();
    GrassSource = nullptr;
}

void AScenarioSession::ResetSession()
{
    bMissionActive = false;
    DroneViewPlayer.Empty();
    FString Error;
    checkf(ResetScenario(Error), TEXT("Session reset failed: %s"), *Error);
    if (!GrassSource)
    {
        GrassSource = NewObject<UPCGGenSourceComponent>(Camera, TEXT("StudioGrassSource"));
        GrassSource->RegisterComponent();
    }
    GetWorld()->GetGameViewport()->bDisableWorldRendering = false;
    UpdateCamera();
    SetActorTickEnabled(true);
}

void AScenarioSession::CameraView(const FString &View)
{
    check(FParse::Param(FCommandLine::Get(), TEXT("ScenarioDiagnostics")));
    FString Error;
    checkf(SetCreatorStage(TEXT("placement"), Error) && SetCamera(View, Error), TEXT("%s"), *Error);
}

void AScenarioSession::CameraTravel(double U, double V)
{
    check(FParse::Param(FCommandLine::Get(), TEXT("ScenarioDiagnostics")));
    FString Error;
    checkf(TravelAndPlace(TEXT("map"), U, V, Error), TEXT("%s"), *Error);
}

void AScenarioSession::CameraNavigation(const FString &Input)
{
    check(FParse::Param(FCommandLine::Get(), TEXT("ScenarioDiagnostics")));
    TSharedPtr<FJsonObject> Value;
    check(FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Input), Value) && Value);
    HandleNavigation(TEXT("PackageCheck"), Value);
}

void AScenarioSession::UpdateCamera()
{
    if (!Camera)
        return;
    if (bFollowPerson)
        CameraTarget = Manager->Person->GetActorLocation() + FVector(0, 0, 100);
    const double Pitch = FMath::DegreesToRadians(CameraPitch), Yaw = FMath::DegreesToRadians(CameraYaw);
    int32 Width = 0, Height = 0;
    UGameplayStatics::GetPlayerController(this, 0)->GetViewportSize(Width, Height);
    check(Width > 0 && Height > 0);
    // The browser covers its viewport with the video. Keep the preview's full
    // vertical composition when a wide window crops the rendered frame.
    const double Framing = bPreviewFraming ? FMath::Max(1., ViewportAspect * Height / Width) : 1.;
    FVector Location = CameraTarget + FVector(FMath::Cos(Yaw) * FMath::Cos(Pitch),
                                              FMath::Sin(Yaw) * FMath::Cos(Pitch), FMath::Sin(Pitch)) *
                                          CameraDistance * Framing;
    FHitResult Hit;
    FCollisionQueryParams Query;
    if (GetWorld()->LineTraceSingleByChannel(Hit, FVector(Location.X, Location.Y, 50000),
                                             FVector(Location.X, Location.Y, -50000), ECC_GameTraceChannel1,
                                             Query))
        Location.Z = FMath::Max(Location.Z, Hit.ImpactPoint.Z + InspectionGroundClearanceCm);
    Camera->SetActorLocationAndRotation(Location, (CameraTarget - Location).Rotation());
    if (auto *PC = UGameplayStatics::GetPlayerController(this, 0))
    {
        PC->bAutoManageActiveCameraTarget = false;
        PC->SetViewTarget(Camera);
    }
}
void AScenarioSession::UpdateMarker()
{
    const bool Show = MarkerMaterial && bShowMarker && Manager && Manager->bPlaced && Manager->Person;
    Marker->SetVisibility(Show);
    if (Show)
    {
        Marker->SetDecalMaterial(MarkerMaterial);
        Marker->SetWorldLocation(Manager->Person->GetActorLocation() + FVector(0, 0, 45));
    }
}
bool AScenarioSession::SetCamera(const FString &View, FString &Error)
{
    bPreviewFraming = View == TEXT("creator");
    if (View != TEXT("explore"))
        bManualHeight = false;
    bFollowPerson = View == TEXT("person") || View == TEXT("ground") || View == TEXT("elevated");
    CameraTransitionDuration = 0;
    if (bNavigationFlight)
        Manager->SetBehaviorActive(true);
    bNavigationFlight = false;
    bBriefingRisePending = false;
    // Shorter people and resting poses need a lower preview camera to keep
    // their whole body in frame. Free exploration has its own eye-height rule.
    InspectionGroundClearanceCm = (View == TEXT("creator") || View == TEXT("portrait")) ? 20. : 100.;
    if (PreviewFill)
        PreviewFill->SetVisibility(View == TEXT("creator") || View == TEXT("portrait"));
    const auto *Region = Manager->Catalog->Regions.FindByPredicate(
        [this](const FScenarioRegion &R) { return R.Id == Manager->RegionId; });
    if (!Region)
    {
        Error = TEXT("Unknown camera region");
        return false;
    }
    StopNavigation();
    if (View == TEXT("start"))
    {
        NavigationMode = 0;
        bNavigationBoost = false;
        const FVector Entry = Region->bUsePreviewCamera ? Region->PreviewCamera.GetLocation()
                                                        : (Region->Min + Region->Max) * .5;
        FHitResult Ground;
        FCollisionQueryParams Query;
        if (!GetWorld()->LineTraceSingleByChannel(Ground, FVector(Entry.X, Entry.Y, Region->Max.Z),
                                                  FVector(Entry.X, Entry.Y, Region->Min.Z),
                                                  ECC_GameTraceChannel1, Query))
        {
            Error = TEXT("The starting camera has no terrain beneath it");
            return false;
        }
        const double Yaw = (Region->PreviewTargetCm - Entry).Rotation().Yaw;
        Camera->SetActorLocationAndRotation(Ground.ImpactPoint + FVector(0, 0, ScenarioNavigation::EyeHeightCm), FRotator(0, Yaw, 0));
        DesiredFieldOfView = ScenarioNavigation::DefaultFov;
        Camera->GetCameraComponent()->SetFieldOfView(DesiredFieldOfView);
        if (auto *PC = UGameplayStatics::GetPlayerController(this, 0))
        {
            PC->bAutoManageActiveCameraTarget = false;
            PC->SetViewTarget(Camera);
        }
        bShowMarker = false;
        UpdateMarker();
        EnterFreeCamera();
        return true;
    }
    if (View == TEXT("explore"))
    {
        EnterFreeCamera();
        return true;
    }
    if (View == TEXT("port"))
    {
        AActor *Port = nullptr;
        for (TActorIterator<AActor> It(GetWorld()); It; ++It)
            if (It->ActorHasTag(TEXT("drone.port")))
            {
                Port = *It;
                break;
            }
        if (!Port)
        {
            Error = TEXT("Drone port is unavailable in this build");
            return false;
        }
        CameraTarget = Port->GetActorLocation() + FVector(0, 0, 20);
        CameraDistance = 2600;
        CameraPitch = 40;
        CameraYaw = -135;
    }
    else if (View == TEXT("overview"))
    {
        CameraTarget = (Region->Min + Region->Max) * .5;
        CameraTarget.Z = 100;
        FHitResult Ground;
        FCollisionQueryParams Query;
        if (GetWorld()->LineTraceSingleByChannel(Ground, FVector(CameraTarget.X, CameraTarget.Y, 50000),
                                                 FVector(CameraTarget.X, CameraTarget.Y, -50000),
                                                 ECC_GameTraceChannel1, Query))
            CameraTarget.Z = Ground.ImpactPoint.Z + 100;
        CameraDistance = 5500;
        CameraPitch = 38;
        CameraYaw = -140;
        if (Region->bUsePreviewCamera)
        {
            CameraTarget = Region->PreviewTargetCm;
            const FVector Offset = Region->PreviewCamera.GetLocation() - CameraTarget;
            CameraDistance = Offset.Size();
            CameraYaw = Offset.Rotation().Yaw;
            CameraPitch = Offset.Rotation().Pitch;
        }
    }
    else if (View == TEXT("departure"))
    {
        check(Manager->bPlaced && Manager->Person);
        CameraTarget = Manager->Person->GetActorLocation();
        CameraDistance = 22000;
        CameraPitch = 72;
        CameraYaw = Camera->GetActorRotation().Yaw - 180.;
    }
    else if (View == TEXT("area"))
    {
        CameraTarget = (Region->Min + Region->Max) * .5;
        CameraTarget.Z = 0;
        CameraDistance =
            FMath::Max(5500., FMath::Max(Region->Max.X - Region->Min.X, Region->Max.Y - Region->Min.Y) * 1.6);
        CameraPitch = 70;
        CameraYaw = -90;
    }
    else if (View == TEXT("creator") || View == TEXT("portrait"))
    {
        if (!Manager->PreviewPerson(Error))
            return false;
        const bool Resting = Manager->SituationId == TEXT("injured");
        const auto *Preset = Manager->Catalog->People.FindByPredicate([this](const FScenarioPersonPreset &P) {
            return P.Id == Manager->Appearance.Options.FindRef(TEXT("personId"));
        });
        check(Preset);
        const double Scale = Preset->HeightCm / 170.;
        CameraTarget =
            Manager->Person->GetActorLocation() +
            FVector(0, 0, Scale * (View == TEXT("portrait") ? (Resting ? 80 : 155) : (Resting ? 65 : 110)));
        CameraYaw = Manager->Person->GetActorRotation().Yaw + 8;
        CameraDistance = (View == TEXT("portrait") ? 150 : 365) * Scale;
        CameraPitch = 2;
        PreviewFill->SetRelativeLocation(FVector(0, -90, 60) * Scale);
        PreviewFill->SetIntensity(200000 * FMath::Square(CameraDistance / 310.));
    }
    else if (View == TEXT("person") || View == TEXT("ground") || View == TEXT("elevated"))
    {
        if (!Manager->bPlaced || !Manager->Person)
        {
            Error = TEXT("Place the person before inspecting");
            return false;
        }
        CameraTarget = Manager->Person->GetActorLocation() + FVector(0, 0, 100);
        CameraYaw = Manager->Person->GetActorRotation().Yaw + 10;
        CameraDistance = View == TEXT("person") ? 400 : View == TEXT("ground") ? 900 : 6500;
        CameraPitch = View == TEXT("elevated") ? 65 : View == TEXT("ground") ? 3 : 8;
    }
    else
    {
        Error = TEXT("Unknown camera view");
        return false;
    }
    bShowMarker = View == TEXT("overview");
    DesiredFieldOfView = ScenarioNavigation::DefaultFov;
    Camera->GetCameraComponent()->SetFieldOfView(DesiredFieldOfView);
    UpdateMarker();
    UpdateCamera();
    bFreeCamera = View == TEXT("overview") || View == TEXT("departure");
    DesiredYaw = Camera->GetActorRotation().Yaw;
    DesiredPitch = Camera->GetActorRotation().Pitch;
    return true;
}

bool AScenarioSession::TransitionCamera(const FString &View, double Duration, FString &Error)
{
    const FTransform Previous = Camera->GetActorTransform();
    const float PreviousFov = Camera->GetCameraComponent()->FieldOfView;
    if (!SetCamera(View, Error))
        return false;
    CameraTransitionFrom = Previous;
    CameraTransitionTo = Camera->GetActorTransform();
    CameraTransitionFov = PreviousFov;
    CameraTransitionStart = FPlatformTime::Seconds();
    CameraTransitionDuration = Duration;
    Camera->SetActorTransform(Previous);
    Camera->GetCameraComponent()->SetFieldOfView(PreviousFov);
    return true;
}

TSharedRef<FJsonObject> AScenarioSession::Packet() const
{
    auto Result = MakeShared<FJsonObject>();
    Result->SetObjectField(TEXT("authoring"), Parse(Manager->ExportAuthoringJson()));
    Result->SetStringField(TEXT("lastSavedId"), LastSavedId);
    Result->SetStringField(TEXT("creatorStage"), CreatorStage);
    Result->SetObjectField(TEXT("briefing"), Briefing.ToSharedRef());
    Result->SetObjectField(TEXT("camera"), CameraPacket());
    auto Catalog = MakeShared<FJsonObject>();
    Catalog->SetStringField(TEXT("environmentId"), Manager->Catalog->EnvironmentId.ToString().ToLower());
    Catalog->SetNumberField(TEXT("navigationVersion"), ScenarioNavigation::Version);
    Catalog->SetObjectField(TEXT("navigation"), NavigationSettings());
    Catalog->SetNumberField(TEXT("creatorVersion"), 1);
    Catalog->SetBoolField(TEXT("droneFoundation"), true);
    Catalog->SetStringField(TEXT("assetVersion"), Manager->Catalog->AssetVersion);
    Catalog->SetStringField(TEXT("coordinateSystem"), TEXT("UE_LH_Z_UP_CENTIMETERS"));
    TArray<TSharedPtr<FJsonValue>> Regions, People, Groups;
    for (const auto &Region : Manager->Catalog->Regions)
    {
        auto Item = MakeShared<FJsonObject>();
        Item->SetStringField(TEXT("id"), Region.Id.ToString().ToLower());
        Item->SetStringField(TEXT("name"), Region.DisplayName.ToString());
        auto Bounds = MakeShared<FJsonObject>();
        Bounds->SetNumberField(TEXT("minX"), Region.Min.X);
        Bounds->SetNumberField(TEXT("maxX"), Region.Max.X);
        Bounds->SetNumberField(TEXT("minY"), Region.Min.Y);
        Bounds->SetNumberField(TEXT("maxY"), Region.Max.Y);
        Item->SetObjectField(TEXT("bounds"), Bounds);
        Regions.Add(MakeShared<FJsonValueObject>(Item));
    }
    for (const auto &Person : Manager->Catalog->People)
    {
        auto Item = MakeShared<FJsonObject>();
        Item->SetStringField(TEXT("id"), Person.Id.ToString().ToLower());
        Item->SetStringField(TEXT("name"), Person.DisplayName.ToString());
        Item->SetStringField(TEXT("ageCategory"), Person.AgeCategory);
        Item->SetStringField(TEXT("description"), Person.Description);
        Item->SetNumberField(TEXT("heightCm"), Person.HeightCm);
        Item->SetStringField(TEXT("topId"), Person.TopId.ToString().ToLower());
        Item->SetStringField(TEXT("bottomId"), Person.BottomId.ToString().ToLower());
        Item->SetBoolField(TEXT("supportsBackpack"), Person.bSupportsBackpack);
        Item->SetBoolField(TEXT("canWalk"), Person.bCanWalk);
        Item->SetBoolField(TEXT("canWave"), Person.bCanWave);
        Item->SetStringField(TEXT("fixedAccessories"), Person.FixedAccessories);
        TArray<TSharedPtr<FJsonValue>> SupportedSituations;
        for (const FName Situation : Person.SupportedSituations)
            SupportedSituations.Add(MakeShared<FJsonValueString>(Situation.ToString().ToLower()));
        Item->SetArrayField(TEXT("supportedSituations"), SupportedSituations);
        People.Add(MakeShared<FJsonValueObject>(Item));
    }
    for (const auto &Group : Manager->Catalog->AppearanceOptions)
    {
        auto Item = MakeShared<FJsonObject>();
        Item->SetStringField(TEXT("key"), FScenarioAppearance::JsonKey(Group.Key));
        TArray<TSharedPtr<FJsonValue>> Values;
        for (auto Value : Group.AllowedValues)
            Values.Add(MakeShared<FJsonValueString>(Value.ToString().ToLower()));
        Item->SetArrayField(TEXT("values"), Values);
        Groups.Add(MakeShared<FJsonValueObject>(Item));
    }
    Catalog->SetArrayField(TEXT("regions"), Regions);
    Catalog->SetArrayField(TEXT("people"), People);
    Catalog->SetArrayField(TEXT("options"), Groups);
    Result->SetObjectField(TEXT("catalog"), Catalog);
    TArray<FString> Files;
    IFileManager::Get().FindFiles(Files, *(Directory() / TEXT("*.json")), true, false);
    TArray<TSharedPtr<FJsonValue>> Scenarios;
    for (const auto &File : Files)
    {
        FString Text;
        if (!FFileHelper::LoadFileToString(Text, *(Directory() / File)) || Text.Len() > 120000)
            continue;
        auto Saved = Parse(Text);
        FString Id, Name, Version;
        if (!Saved || !Saved->TryGetStringField(TEXT("id"), Id) || !ValidId(Id) ||
            !Saved->TryGetStringField(TEXT("name"), Name) ||
            !Saved->TryGetStringField(TEXT("assetVersion"), Version) ||
            Version != Manager->Catalog->AssetVersion)
            continue;
        auto Item = MakeShared<FJsonObject>();
        Item->SetStringField(TEXT("id"), Id);
        Item->SetStringField(TEXT("name"), Name);
        Scenarios.Add(MakeShared<FJsonValueObject>(Item));
    }
    Result->SetArrayField(TEXT("scenarios"), Scenarios);
    return Result;
}
bool AScenarioSession::Save(const FString &Name, const FString &Summary, FString &Error)
{
    if (!Manager->bPlaced || !Manager->Person)
    {
        Error = TEXT("Place the person before saving");
        return false;
    }
    if (Name.TrimStartAndEnd().IsEmpty() || Name.Len() > 80 || Summary.Len() > 500)
    {
        Error = TEXT("Invalid scenario description");
        return false;
    }
    auto Snapshot = MakeShared<FJsonObject>();
    const FString Id = FGuid::NewGuid().ToString(EGuidFormats::DigitsWithHyphens);
    Snapshot->SetStringField(TEXT("id"), Id);
    Snapshot->SetStringField(TEXT("name"), Name.TrimStartAndEnd());
    Snapshot->SetNumberField(TEXT("schemaVersion"), 3);
    Snapshot->SetStringField(TEXT("createdAt"), FDateTime::UtcNow().ToIso8601());
    Snapshot->SetStringField(TEXT("environmentId"), Manager->Catalog->EnvironmentId.ToString().ToLower());
    Snapshot->SetStringField(TEXT("assetVersion"), Manager->Catalog->AssetVersion);
    Snapshot->SetStringField(TEXT("coordinateSystem"), TEXT("UE_LH_Z_UP_CENTIMETERS"));
    Snapshot->SetNumberField(TEXT("randomSeed"), 74021);
    Snapshot->SetStringField(TEXT("renderer"), TEXT("unreal"));
    Snapshot->SetObjectField(TEXT("authoring"), Parse(Manager->ExportAuthoringJson()));
    auto Public = MakeShared<FJsonObject>();
    Public->SetStringField(TEXT("summary"), Summary);
    Public->SetStringField(TEXT("regionId"), Manager->RegionId.ToString().ToLower());
    Snapshot->SetObjectField(TEXT("publicDescription"), Public);
    Snapshot->SetObjectField(TEXT("briefing"), Briefing.ToSharedRef());
    Snapshot->SetStringField(TEXT("creatorStage"), CreatorStage);
    auto Case = PublicBriefing();
    Case->SetStringField(TEXT("id"), Id);
    Case->SetStringField(TEXT("name"), Name.TrimStartAndEnd());
    const FString PublicDir = Directory() / TEXT("briefings");
    IFileManager::Get().MakeDirectory(*PublicDir, true);
    const FString PublicPath = PublicDir / (Id + TEXT(".json"));
    if (!FFileHelper::SaveStringToFile(Stringify(Case), *(PublicPath + TEXT(".tmp")),
                                       FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM) ||
        !IFileManager::Get().Move(*PublicPath, *(PublicPath + TEXT(".tmp")), false))
    {
        Error = TEXT("Could not persist the rescue briefing");
        return false;
    }
    IFileManager::Get().MakeDirectory(*Directory(), true);
    const FString Final = Directory() / (Id + TEXT(".json")), Temporary = Final + TEXT(".tmp");
    if (!FFileHelper::SaveStringToFile(Stringify(Snapshot), *Temporary,
                                       FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM) ||
        !IFileManager::Get().Move(*Final, *Temporary, false))
    {
        IFileManager::Get().Delete(*PublicPath);
        Error = TEXT("Could not persist the scenario");
        return false;
    }
    LastSavedId = Id;
    return true;
}
bool AScenarioSession::Load(const FString &Id, FString &Error)
{
    if (!ValidId(Id))
    {
        Error = TEXT("Invalid scenario identifier");
        return false;
    }
    FString Json;
    if (!FFileHelper::LoadFileToString(Json, *(Directory() / (Id + TEXT(".json")))) || Json.Len() > 120000)
    {
        Error = TEXT("Scenario not found");
        return false;
    }
    auto Saved = Parse(Json);
    const TSharedPtr<FJsonObject> *SavedBriefing;
    FString Stage = TEXT("placement");
    double Schema;
    if (!Saved || !Saved->TryGetNumberField(TEXT("schemaVersion"), Schema) ||
        (Schema != 1 && Schema != 2 && Schema != 3))
    {
        Error = TEXT("Invalid saved scenario");
        return false;
    }
    auto PreviousBriefing = Briefing;
    if (Schema >= 2)
    {
        if (!Saved->TryGetObjectField(TEXT("briefing"), SavedBriefing) ||
            !SetBriefing(*SavedBriefing, Error) || !Saved->TryGetStringField(TEXT("creatorStage"), Stage) ||
            (Stage != TEXT("person") && Stage != TEXT("situation") && Stage != TEXT("placement") &&
             Stage != TEXT("briefing")))
        {
            Briefing = PreviousBriefing;
            Error = TEXT("Invalid saved creator state");
            return false;
        }
    }
    else
    {
        Briefing = MakeShared<FJsonObject>();
        for (const TCHAR *Key : {TEXT("brief"), TEXT("portrait")})
            Briefing->SetStringField(Key, TEXT(""));
    }
    if (!Manager->RestoreSnapshot(Json, Error))
    {
        Briefing = PreviousBriefing;
        return false;
    }
    LastSavedId = Id;
    return SetCreatorStage(Stage, Error);
}

void AScenarioSession::HandleCommand(const FString &Player, const FString &Message)
{
    auto Streamer = BoundStreamer.Pin();
    if (!Streamer || !Manager || !Manager->Catalog)
        return;
    auto Root = Parse(Message);
    FString InputType;
    if (Root && Root->TryGetStringField(TEXT("type"), InputType) && InputType == TEXT("navigation"))
    {
        HandleNavigation(Player, Root);
        return;
    }
    FString RequestId;
    double Protocol;
    const TSharedPtr<FJsonObject> *Command;
    if (!Fields(Root, {TEXT("protocolVersion"), TEXT("requestId"), TEXT("command")}) ||
        !Root->TryGetStringField(TEXT("requestId"), RequestId) || !ValidId(RequestId) ||
        !Root->TryGetNumberField(TEXT("protocolVersion"), Protocol) || Protocol != ScenarioNavigation::ProtocolVersion ||
        !Root->TryGetObjectField(TEXT("command"), Command))
        return;
    if (!bMissionActive)
        NavigationPlayer = Player;
    FString Type, Error, DroneId;
    bool Ok = false;
    if ((*Command)->TryGetStringField(TEXT("type"), Type))
    {
        FString Value;
        double A, B, C;
        if (Type == TEXT("drone-view"))
        {
            if (!Fields(*Command, {TEXT("type"), TEXT("droneId")}) ||
                !(*Command)->TryGetStringField(TEXT("droneId"), DroneId) ||
                (DroneId != TEXT("Drone1") && DroneId != TEXT("Drone2") &&
                 DroneId != TEXT("Drone3") && DroneId != TEXT("Drone4")))
                Error = TEXT("drone-view requires a droneId from Drone1 to Drone4");
            else if (!bMissionActive)
                Error = TEXT("Drone video requires an active mission");
            else
            {
                TActorIterator<ASimModeBase> Mode(GetWorld());
                auto *Viewport = GetWorld()->GetGameViewport();
                APIPCamera *Sensor = Mode ? Mode->getCamera(
                    msr::airlib::CameraDetails("fpv", TCHAR_TO_UTF8(*DroneId))) : nullptr;
                if (!Sensor || !Viewport || !GetWorld()->GetFirstPlayerController())
                    Error = TEXT("Drone camera is unavailable");
                else
                {
                    Sensor->showToScreen();
                    Viewport->bDisableWorldRendering = false;
                    DroneViewPlayer = Player;
                    Ok = true;
                }
            }
        }
        else if (bMissionActive)
            Error = TEXT("This scenario has launched. Return to Studio to cancel it and start over.");
        else if (Type == TEXT("viewport") && Fields(*Command, {TEXT("type"), TEXT("aspect")}) &&
            (*Command)->TryGetNumberField(TEXT("aspect"), A) && FMath::IsFinite(A) && A >= .25 && A <= 4.)
        {
            ViewportAspect = A;
            if (bPreviewFraming)
                UpdateCamera();
            Ok = true;
        }
        else if (Type == TEXT("snapshot") && Fields(*Command, {TEXT("type")}))
            Ok = true;
        else if (Type == TEXT("portrait") && Fields(*Command, {TEXT("type")}))
            Ok = CapturePortrait(Error);
        else if (Type == TEXT("creator-stage") && Fields(*Command, {TEXT("type"), TEXT("stage")}) &&
                 (*Command)->TryGetStringField(TEXT("stage"), Value))
            Ok = SetCreatorStage(Value, Error);
        else if (Type == TEXT("situation") && Fields(*Command, {TEXT("type"), TEXT("situationId")}) &&
                 (*Command)->TryGetStringField(TEXT("situationId"), Value))
        {
            Ok = Manager->SetSituation(FName(*Value), Error);
            if (Ok && (CreatorStage == TEXT("person") || CreatorStage == TEXT("situation")))
                Ok = SetCamera(TEXT("creator"), Error);
        }
        else if (Type == TEXT("briefing") && Fields(*Command, {TEXT("type"), TEXT("briefing")}))
        {
            const TSharedPtr<FJsonObject> *Report;
            if ((*Command)->TryGetObjectField(TEXT("briefing"), Report))
                Ok = SetBriefing(*Report, Error);
        }
        else if (Type == TEXT("reset") && Fields(*Command, {TEXT("type")}))
            Ok = ResetScenario(Error);
        else if (Type == TEXT("region") && Fields(*Command, {TEXT("type"), TEXT("regionId")}) &&
                 (*Command)->TryGetStringField(TEXT("regionId"), Value) && Value.Len() <= 48)
        {
            Ok = Manager->SelectRegion(FName(*Value), Error);
            if (Ok)
                SetCamera(TEXT("overview"), Error);
        }
        else if (Type == TEXT("rotate") && Fields(*Command, {TEXT("type"), TEXT("yaw")}) &&
                 (*Command)->TryGetNumberField(TEXT("yaw"), A))
            Ok = Manager->SetYaw(A, Error);
        else if (Type == TEXT("travel-and-place") &&
                 Fields(*Command, {TEXT("type"), TEXT("space"), TEXT("u"), TEXT("v")}) &&
                 (*Command)->TryGetStringField(TEXT("space"), Value) &&
                 (*Command)->TryGetNumberField(TEXT("u"), A) && (*Command)->TryGetNumberField(TEXT("v"), B))
            Ok = TravelAndPlace(Value, A, B, Error);
        else if (Type == TEXT("camera") && Fields(*Command, {TEXT("type"), TEXT("view")}) &&
                 (*Command)->TryGetStringField(TEXT("view"), Value) && Value != TEXT("departure"))
            Ok = SetCamera(Value, Error);
        else if (Type == TEXT("orbit") &&
                 Fields(*Command, {TEXT("type"), TEXT("yawDelta"), TEXT("pitchDelta"), TEXT("zoomFactor")}) &&
                 (*Command)->TryGetNumberField(TEXT("yawDelta"), A) &&
                 (*Command)->TryGetNumberField(TEXT("pitchDelta"), B) &&
                 (*Command)->TryGetNumberField(TEXT("zoomFactor"), C) && FMath::IsFinite(A) &&
                 FMath::IsFinite(B) && FMath::IsFinite(C) && FMath::Abs(A) <= 30 && FMath::Abs(B) <= 30 &&
                 C >= .8 && C <= 1.25)
        {
            CameraYaw += A;
            CameraPitch = FMath::Clamp(CameraPitch + B, -5., 80.);
            DesiredFieldOfView = FMath::Clamp(DesiredFieldOfView * C, 20., 90.);
            UpdateCamera();
            bFreeCamera = false;
            DesiredYaw = Camera->GetActorRotation().Yaw;
            DesiredPitch = Camera->GetActorRotation().Pitch;
            Ok = true;
        }
        else if (Type == TEXT("appearance") && Fields(*Command, {TEXT("type"), TEXT("appearance")}))
        {
            const TSharedPtr<FJsonObject> *Options;
            if ((*Command)->TryGetObjectField(TEXT("appearance"), Options) && (*Options)->Values.Num() <= 20)
            {
                FScenarioAppearance Selection;
                bool Valid = true;
                for (const auto &Pair : (*Options)->Values)
                {
                    FString Choice;
                    if (Pair.Key.Len() > 48 || !Pair.Value->TryGetString(Choice) || Choice.Len() > 48)
                    {
                        Valid = false;
                        break;
                    }
                    Selection.Options.Add(FName(*Pair.Key), FName(*Choice));
                }
                if (Valid)
                {
                    const bool ChangedPerson = Manager->Appearance.Options.FindRef(TEXT("personId")) !=
                                               Selection.Options.FindRef(TEXT("personId"));
                    Ok = Manager->SetAppearance(Selection, Error);
                    if (Ok)
                    {
                        Briefing->SetStringField(TEXT("portrait"), TEXT(""));
                        if (CreatorStage == TEXT("person") || CreatorStage == TEXT("situation"))
                            Ok = ChangedPerson ? SetCamera(TEXT("creator"), Error)
                                               : Manager->PreviewPerson(Error);
                    }
                }
            }
        }
        else if (Type == TEXT("save") && Fields(*Command, {TEXT("type"), TEXT("name"), TEXT("summary")}))
        {
            FString Summary;
            if ((*Command)->TryGetStringField(TEXT("name"), Value) &&
                (*Command)->TryGetStringField(TEXT("summary"), Summary))
                Ok = Save(Value, Summary, Error);
        }
        else if (Type == TEXT("load") && Fields(*Command, {TEXT("type"), TEXT("id")}) &&
                 (*Command)->TryGetStringField(TEXT("id"), Value))
            Ok = Load(Value, Error);
    }
    if (Ok && Type != TEXT("drone-view"))
    {
        if (Type == TEXT("travel-and-place"))
            bShowMarker = CreatorStage == TEXT("placement");
        UpdateMarker();
    }
    auto Reply = MakeShared<FJsonObject>();
    Reply->SetNumberField(TEXT("protocolVersion"), ScenarioNavigation::ProtocolVersion);
    Reply->SetStringField(TEXT("requestId"), RequestId);
    Reply->SetBoolField(TEXT("ok"), Ok);
    if (Ok)
    {
        if (Type == TEXT("drone-view"))
        {
            auto State = MakeShared<FJsonObject>();
            State->SetStringField(TEXT("droneId"), DroneId);
            Reply->SetObjectField(TEXT("state"), State);
        }
        else
            Reply->SetObjectField(TEXT("state"), Packet());
    }
    else
        Reply->SetStringField(TEXT("error"),
                              Error.IsEmpty() ? TEXT("Unsupported or malformed command") : Error);
    Streamer->SendPlayerMessage(Player, TEXT("Response"), Stringify(Reply));
}
