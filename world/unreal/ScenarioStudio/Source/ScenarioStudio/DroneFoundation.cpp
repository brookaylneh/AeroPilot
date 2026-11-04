#include "DroneFoundation.h"
#include "ScenarioSession.h"
#include "ScenarioGrass.h"
#include "RuntimeGen/GenSources/PCGGenSourceComponent.h"
#include "Components/PrimitiveComponent.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "UObject/UObjectIterator.h"
#include "UObject/UnrealType.h"
#include "EngineUtils.h"
#include "Engine/World.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/StaticMesh.h"
#include "Engine/LevelStreamingDynamic.h"
#include "Engine/Level.h"
#include "Engine/TextureRenderTarget2D.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "HAL/FileManager.h"
#include "HAL/IConsoleManager.h"
#include "HAL/PlatformFileManager.h"
#include "Serialization/JsonSerializer.h"
#include "TimerManager.h"
#include "RenderTimer.h"
#include "DynamicRHI.h"
#include "ContentStreaming.h"
#include "Engine/GameViewportClient.h"
#include "GameFramework/PlayerController.h"
#include "SimMode/SimModeBase.h"
#include "Vehicles/Multirotor/FlyingPawn.h"

namespace
{
TSharedRef<FJsonObject> V(const FVector &P)
{
    auto J = MakeShared<FJsonObject>();
    J->SetNumberField(TEXT("x"), P.X);
    J->SetNumberField(TEXT("y"), P.Y);
    J->SetNumberField(TEXT("z"), P.Z);
    return J;
}
void Write(const FString &Path, const TSharedRef<FJsonObject> &J)
{
    FString S;
    const bool Serialized = FJsonSerializer::Serialize(J, TJsonWriterFactory<>::Create(&S));
    checkf(Serialized, TEXT("Could not serialize native telemetry"));
    const FString Temporary = Path + TEXT(".tmp");
    // IFileManager::Move deletes the destination first; Linux MoveFile renames atomically.
    if (!FFileHelper::SaveStringToFile(S, *Temporary) ||
        !FPlatformFileManager::Get().GetPlatformFile().MoveFile(*Path, *Temporary))
        UE_LOG(LogTemp, Fatal, TEXT("Could not publish native telemetry: %s"), *Path);
}
TSharedRef<FJsonObject> PlaceInfrastructure(UWorld *World)
{
    FString Path, Text;
    TSharedPtr<FJsonObject> Config;
    checkf(FParse::Value(FCommandLine::Get(), TEXT("ScenarioWorldConfig="), Path) &&
               FFileHelper::LoadFileToString(Text, *Path) &&
               FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Config) && Config,
           TEXT("The release world manifest is required"));
    const auto Port = Config->GetObjectField(TEXT("port"));
    const auto Position = Port->GetObjectField(TEXT("centerUeCm"));
    FVector Top;
    check(Position->Values.Num() == 3 && Position->TryGetNumberField(TEXT("x"), Top.X) &&
          Position->TryGetNumberField(TEXT("y"), Top.Y) && Position->TryGetNumberField(TEXT("z"), Top.Z));
    check(!Top.ContainsNaN());
    const auto Dimensions = Port->GetArrayField(TEXT("dimensionsMetres"));
    check(Dimensions.Num() == 3);
    AStaticMeshActor *Pad = nullptr;
    for (TActorIterator<AStaticMeshActor> It(World); It; ++It)
        if (It->ActorHasTag(TEXT("drone.port")))
        {
            check(!Pad);
            Pad = *It;
        }
    checkf(Pad, TEXT("The authored drone port is missing"));
    FVector Center, Extent;
    Pad->GetActorBounds(false, Center, Extent);
    for (int32 Axis = 0; Axis != 3; ++Axis)
        check(FMath::IsNearlyEqual(Extent[Axis], Dimensions[Axis]->AsNumber() * 50., .01));
    const FVector Delta = Top - Center - FVector(0, 0, Extent.Z);
    auto *Mesh = Pad->GetStaticMeshComponent();
    Mesh->SetMobility(EComponentMobility::Movable);
    check(Pad->SetActorLocation(Pad->GetActorLocation() + Delta, false, nullptr, ETeleportType::TeleportPhysics));
    Mesh->SetMobility(EComponentMobility::Static);
    FVector PadCenter, PadExtent;
    Pad->GetActorBounds(false, PadCenter, PadExtent);
    const FVector PadTop = PadCenter + FVector(0, 0, PadExtent.Z);
    check(PadTop.Equals(Top, .01));
    TArray<TSharedPtr<FJsonValue>> Mats;
    for (TObjectIterator<UInstancedStaticMeshComponent> It; It; ++It)
        if (It->GetWorld() == World && It->GetStaticMesh() &&
            It->GetStaticMesh()->GetFName() == TEXT("SM_Landing_Mat"))
        {
            check(It->GetInstanceCount() == 4 && Mats.IsEmpty());
            for (int32 Index = 0; Index != 4; ++Index)
            {
                FTransform Transform;
                check(It->GetInstanceTransform(Index, Transform, true));
                const FVector Original = Transform.GetLocation();
                check(FMath::Abs(Original.X - Center.X) < Extent.X &&
                      FMath::Abs(Original.Y - Center.Y) < Extent.Y);
                Transform.AddToTranslation(Delta);
                check(It->UpdateInstanceTransform(Index, Transform, true, true, true));
                check(It->GetInstanceTransform(Index, Transform, true));
                check(FMath::IsNearlyEqual(
                    It->GetStaticMesh()->GetBoundingBox().TransformBy(Transform).Min.Z, Top.Z, .01));
                Mats.Add(MakeShared<FJsonValueObject>(V(Transform.GetLocation())));
            }
            CastChecked<UHierarchicalInstancedStaticMeshComponent>(*It)->BuildTreeIfOutdated(false, false);
        }
    checkf(Mats.Num() == 4, TEXT("All four authored landing mats are required"));
    const auto Vehicles = Config->GetObjectField(TEXT("vehicles"));
    check(Vehicles->Values.Num() == 4);
    for (const auto &Vehicle : Vehicles->Values)
    {
        const auto Spawn = Vehicle.Value->AsObject()->GetObjectField(TEXT("spawnUeCm"));
        check(FMath::IsNearlyEqual(Spawn->GetNumberField(TEXT("z")), Top.Z + 20., .01));
        check(Mats.ContainsByPredicate([&](const auto &Mat) {
            const auto Point = Mat->AsObject();
            return FMath::IsNearlyEqual(Point->GetNumberField(TEXT("x")), Spawn->GetNumberField(TEXT("x")), .01) &&
                   FMath::IsNearlyEqual(Point->GetNumberField(TEXT("y")), Spawn->GetNumberField(TEXT("y")), .01);
        }));
    }
    bool Loaded = false;
    auto *Wildlife = ULevelStreamingDynamic::LoadLevelInstance(
        World, TEXT("/Wildlife/BearHabitat"), FVector::ZeroVector, FRotator::ZeroRotator, Loaded);
    checkf(Loaded && Wildlife, TEXT("The wildlife content package is missing"));
    Wildlife->bShouldBlockOnLoad = true;
    World->FlushLevelStreaming(EFlushLevelStreamingType::Full);
    check(Wildlife->GetLoadedLevel() && Wildlife->IsLevelVisible());
    AActor *Bear = nullptr;
    for (AActor *Actor : Wildlife->GetLoadedLevel()->Actors)
        if (Actor && Actor->ActorHasTag(TEXT("wildlife.bear")))
        {
            check(!Bear);
            Bear = Actor;
        }
    check(Bear);
    Bear->GetActorBounds(false, Center, Extent);
    auto Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("infrastructureVersion"), Config->GetStringField(TEXT("infrastructureVersion")));
    Report->SetObjectField(TEXT("portTopUeCm"), V(PadTop));
    Report->SetArrayField(TEXT("landingMatsUeCm"), Mats);
    Report->SetObjectField(TEXT("bearCenterUeCm"), V(Center));
    Report->SetObjectField(TEXT("bearExtentCm"), V(Extent));
    return Report;
}
}
ADroneFoundation::ADroneFoundation()
{
    PrimaryActorTick.bCanEverTick = false;
}
void ADroneFoundation::PostInitializeComponents()
{
    Super::PostInitializeComponents();
    if (!GetWorld()->IsGameWorld())
        return;
    if (!FParse::Value(FCommandLine::Get(), TEXT("DroneFoundationDir="), Directory) || Directory.IsEmpty())
        return;
    Directory = FPaths::ConvertRelativePathToFull(Directory);
    IFileManager::Get().MakeDirectory(*Directory, true);
    // AirSim creates the aircraft in BeginPlay, so their slab must already be in place.
    GetWorld()->OnWorldPreBeginPlay.AddWeakLambda(this, [this]() {
        Write(Directory / TEXT("infrastructure.json"), PlaceInfrastructure(GetWorld()));
        bInfrastructureReady = true;
    });
}
void ADroneFoundation::BeginPlay()
{
    Super::BeginPlay();
    if (Directory.IsEmpty())
        return;
    checkf(bInfrastructureReady, TEXT("Infrastructure must be placed before aircraft initialization"));
    // A mission request belongs to the world that consumed it; every world starts in studio mode.
    checkf(IFileManager::Get().Delete(*(Directory / TEXT("mission-request.json")), false, false, true),
           TEXT("Cannot clear the previous mission request"));
    GetWorldTimerManager().SetTimer(Timer, this, &ADroneFoundation::Poll, .5f, true, 3.f);
}
void ADroneFoundation::EndPlay(const EEndPlayReason::Type Reason)
{
    GetWorldTimerManager().ClearTimer(Timer);
    Super::EndPlay(Reason);
}
void ADroneFoundation::EnterMission(ASimModeBase *Mode)
{
    APIPCamera *Camera = Mode->getCamera(msr::airlib::CameraDetails("front_center", "Drone1"));
    APlayerController *Player = GetWorld()->GetFirstPlayerController();
    UGameViewportClient *Viewport = GetWorld()->GetGameViewport();
    TActorIterator<AScenarioSession> Session(GetWorld());
    TActorIterator<AScenarioGrass> Grass(GetWorld());
    checkf(Camera && Player && Viewport && Session && Grass, TEXT("Mission mode prerequisites are missing"));
    Session->DisableGrassSource();
    Grass->EnterMission();
    for (int32 Index = 1; Index <= 4; ++Index)
    {
        const FString Name = FString::Printf(TEXT("Drone%d"), Index);
        APIPCamera *Sensor = Mode->getCamera(msr::airlib::CameraDetails("front_center", TCHAR_TO_UTF8(*Name)));
        check(Sensor);
        UPCGGenSourceComponent *Source = NewObject<UPCGGenSourceComponent>(Sensor, TEXT("MissionGrassSource"));
        Source->RegisterComponent();
    }
    Player->SetViewTarget(Camera);
    Viewport->bDisableWorldRendering = true;
    bMission = true;
}
void ADroneFoundation::Poll()
{
    TActorIterator<ASimModeBase> Modes(GetWorld());
    if (!Modes)
        return;
    ASimModeBase *Mode = *Modes;
    FString Text;
    TSharedPtr<FJsonObject> Command;
    if (FFileHelper::LoadFileToString(Text, *(Directory / TEXT("mission-request.json"))))
    {
        FString Requested, Id;
        checkf(FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Command) && Command &&
                   Command->Values.Num() == 2 && Command->TryGetStringField(TEXT("mode"), Requested) &&
                   Command->TryGetStringField(TEXT("id"), Id) && !Id.IsEmpty() && Id.Len() <= 64 &&
                   (Requested == TEXT("mission") || Requested == TEXT("studio")),
               TEXT("Invalid mission mode request"));
        if (Id != LastModeRequest)
        {
            if (Requested == TEXT("mission"))
            {
                check(!bMission);
                EnterMission(Mode);
            }
            else
            {
                if (bMission)
                {
                    for (int32 Index = 1; Index <= 4; ++Index)
                    {
                        const FString Name = FString::Printf(TEXT("Drone%d"), Index);
                        APIPCamera *Sensor = Mode->getCamera(msr::airlib::CameraDetails("front_center", TCHAR_TO_UTF8(*Name)));
                        check(Sensor);
                        auto *Source = Sensor->FindComponentByClass<UPCGGenSourceComponent>();
                        check(Source);
                        Source->DestroyComponent();
                    }
                    TActorIterator<AScenarioGrass> Grass(GetWorld());
                    check(Grass);
                    Grass->EnterStudio();
                }
                TActorIterator<AScenarioSession> Session(GetWorld());
                check(Session);
                Session->ResetSession();
                bMission = false;
            }
            LastModeRequest = Id;
        }
    }
    if (FFileHelper::LoadFileToString(Text, *(Directory / TEXT("capture-request.json"))) &&
        FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Command) && Command)
    {
        FString Id;
        double Height = 0;
        if (Command->Values.Num() == 2 && Command->TryGetStringField(TEXT("id"), Id) && Id.Len() <= 64 &&
            Id != LastRequest && Command->TryGetNumberField(TEXT("height"), Height) &&
            (Height == 1080 || Height == 2160 || Height == 4320))
        {
            CaptureHeight = static_cast<int32>(Height);
            LastRequest = Id;
        }
    }
    auto Report = MakeShared<FJsonObject>();
    Report->SetNumberField(TEXT("schemaVersion"), 1);
    TActorIterator<AScenarioSession> Session(GetWorld());
    Report->SetBoolField(TEXT("scenarioReady"), Session && Session->IsReady());
    Report->SetStringField(TEXT("capturedAt"), FDateTime::UtcNow().ToIso8601());
    Report->SetObjectField(TEXT("globalNedOriginUeCm"), V(Mode->getGlobalNedTransform().getGlobalOffset()));
    Report->SetObjectField(TEXT("worldOriginOffsetCm"), V(FVector(GetWorld()->OriginLocation)));
    Report->SetNumberField(TEXT("worldToMeters"), GetWorld()->GetWorldSettings()->WorldToMeters);
    Report->SetStringField(TEXT("captureRequestId"), LastRequest);
    Report->SetStringField(TEXT("modeRequestId"), LastModeRequest);
    Report->SetStringField(TEXT("mode"), bMission ? TEXT("mission") : TEXT("studio"));
    // The capture opt-in below is fixed; these scalability cvars switch Lumen off for every view,
    // falling back to sky light and screen-space reflections (the measured A/B).
    const auto &Console = IConsoleManager::Get();
    const IConsoleVariable *LumenGi = Console.FindConsoleVariable(TEXT("r.Lumen.DiffuseIndirect.Allow"));
    const IConsoleVariable *LumenReflections = Console.FindConsoleVariable(TEXT("r.Lumen.Reflections.Allow"));
    checkf(LumenGi && LumenReflections, TEXT("Missing Lumen scalability cvars"));
    Report->SetBoolField(TEXT("lumenGlobalIllumination"), LumenGi->GetInt() != 0);
    Report->SetBoolField(TEXT("lumenReflections"), LumenReflections->GetInt() != 0);
    TArray<TSharedPtr<FJsonValue>> Vehicles;
    for (TActorIterator<AFlyingPawn> It(GetWorld()); It; ++It)
    {
        const FString Name = It->GetName();
        if (Name != TEXT("Drone1") && Name != TEXT("Drone2") && Name != TEXT("Drone3") &&
            Name != TEXT("Drone4"))
            continue;
        auto *Api = Mode->getVehicleSimApi(TCHAR_TO_UTF8(*Name));
        if (!Api)
            continue;
        auto Item = MakeShared<FJsonObject>();
        Item->SetStringField(TEXT("id"), Name);
        const auto Local = Api->getPose().position;
        // Record at initialization, before the controller is admitted. Do not
        // recompute from physics and rendered poses while a vehicle is moving:
        // those samples can be from different engine frames.
        if (!VehicleOrigins.Contains(Name))
            VehicleOrigins.Add(Name,
                               It->GetActorLocation() - FVector(Local.x(), Local.y(), -Local.z()) * 100.);
        Item->SetObjectField(TEXT("localNedOriginUeCm"), V(VehicleOrigins[Name]));
        Item->SetObjectField(TEXT("positionUeCm"), V(It->GetActorLocation()));
        Item->SetObjectField(TEXT("rotationDegrees"),
                             V(FVector(It->GetActorRotation().Roll, It->GetActorRotation().Pitch,
                                       It->GetActorRotation().Yaw)));
        FVector Center, Extent;
        It->GetActorBounds(true, Center, Extent);
        Item->SetObjectField(TEXT("collisionHalfExtentCm"), V(Extent));
        // Search uses configured front_center; the viewport uses stock fpv.
        // Settings replace only named registry entries. Resolve through the RPC registry.
        APIPCamera *Camera =
            Mode->getCamera(msr::airlib::CameraDetails("front_center", TCHAR_TO_UTF8(*Name)));
        checkf(Camera, TEXT("Missing front_center camera for %s"), *Name);
        {
            auto *Capture = Camera->getCaptureComponent(APIPCamera::ImageType::Scene, false);
            checkf(Capture, TEXT("Missing RGB capture component for %s"), *Name);
            check(!Capture->ShouldRenderWithMainViewResolution());
            const float Width = CaptureHeight * 16.f / 9.f;
            const float Fov = Capture->FOVAngle;
            // Scene captures need their own texture streaming views. PCG has explicit camera sources.
            if (bMission)
                IStreamingManager::Get().AddViewInformation(
                    Camera->GetActorLocation(), Width, Width / FMath::Tan(FMath::DegreesToRadians(Fov * .5f)),
                    1.f, false, .7f, nullptr, GetWorld());
            auto *Target = Camera->getRenderTarget(APIPCamera::ImageType::Scene, false);
            checkf(Target, TEXT("Missing RGB render target for %s"), *Name);
            if (Target->SizeY != CaptureHeight)
                Target->ResizeTarget(CaptureHeight * 16 / 9, CaptureHeight);
            Camera->setCameraTypeUpdate(APIPCamera::ImageType::Scene,
                                        true); // request-driven, preserve temporal history
            const auto &Settings = Capture->PostProcessSettings;
            // Independent scene captures disable Lumen before component post-process overrides.
            checkf(Settings.bOverride_DynamicGlobalIlluminationMethod &&
                       Settings.DynamicGlobalIlluminationMethod == EDynamicGlobalIlluminationMethod::Lumen &&
                       Settings.bOverride_ReflectionMethod &&
                       Settings.ReflectionMethod == EReflectionMethod::Lumen &&
                       Capture->PostProcessBlendWeight == 1.f,
                   TEXT("RGB capture must explicitly enable Lumen: %s"), *Name);
            checkf(Capture->bAlwaysPersistRenderingState && Capture->bUseRayTracingIfEnabled &&
                       Capture->CaptureSource == ESceneCaptureSource::SCS_FinalToneCurveHDR &&
                       Capture->ShowFlags.GlobalIllumination && Capture->ShowFlags.LumenGlobalIllumination &&
                       Capture->ShowFlags.LumenReflections && Capture->ShowFlags.DynamicShadows,
                   TEXT("RGB capture rendering prerequisites disabled: %s"), *Name);
            checkf(Settings.bOverride_AutoExposureMethod && Settings.AutoExposureMethod == AEM_Manual &&
                       Settings.bOverride_AutoExposureBias && Settings.bOverride_WhiteTemp &&
                       Settings.bOverride_WhiteTint && Settings.BloomIntensity == 0.f &&
                       Settings.MotionBlurAmount == 0.f,
                   TEXT("RGB capture exposure, white balance or effects are not fixed: %s"), *Name);
            Item->SetStringField(TEXT("lighting"), TEXT("lumen"));
            Item->SetNumberField(TEXT("exposureCompensation"), Settings.AutoExposureBias);
            Item->SetNumberField(TEXT("whiteBalanceKelvin"), Settings.WhiteTemp);
            Item->SetNumberField(TEXT("whiteBalanceTint"), Settings.WhiteTint);
            Item->SetNumberField(TEXT("width"), Target->SizeX);
            Item->SetNumberField(TEXT("height"), Target->SizeY);
            Item->SetObjectField(TEXT("cameraPositionUeCm"), V(Camera->GetActorLocation()));
        }
        Vehicles.Add(MakeShared<FJsonValueObject>(Item));
    }
    Report->SetArrayField(TEXT("vehicles"), Vehicles);
    auto Timing = MakeShared<FJsonObject>();
    Timing->SetNumberField(TEXT("frameMs"), GetWorld()->GetDeltaSeconds() * 1000.);
    Timing->SetNumberField(TEXT("gameMs"), FPlatformTime::ToMilliseconds(GGameThreadTime));
    Timing->SetNumberField(TEXT("renderMs"), FPlatformTime::ToMilliseconds(GRenderThreadTime));
    Timing->SetNumberField(TEXT("gpuMs"), FPlatformTime::ToMilliseconds(RHIGetGPUFrameCycles()));
    Report->SetObjectField(TEXT("timing"), Timing);
    if (Session)
    {
        Report->SetObjectField(TEXT("camera"), Session->CameraPacket());
        Report->SetStringField(TEXT("sessionActor"), Session->GetName());
    }
    // PCG's procedural component is private to its module; its reflected count is the GPU
    // allocation size, not a readback of generated point data.
    UClass *ProceduralClass = FindObject<UClass>(nullptr, TEXT("/Script/PCG.PCGProceduralISMComponent"));
    check(ProceduralClass);
    const FIntProperty *Count = FindFProperty<FIntProperty>(ProceduralClass, TEXT("NumInstances"));
    check(Count);
    int64 Allocated = 0, SceneInstances = 0;
    for (TObjectIterator<UPrimitiveComponent> It; It; ++It)
        if (IsValid(*It) && It->IsRegistered() && It->GetWorld() == GetWorld())
        {
            if (It->IsA(ProceduralClass))
            {
                const int32 Instances = Count->GetPropertyValue_InContainer(*It);
                Allocated += Instances;
                SceneInstances += Instances;
            }
            else if (const UInstancedStaticMeshComponent *Instances = Cast<UInstancedStaticMeshComponent>(*It))
                SceneInstances += Instances->GetNumRenderInstances();
            else
                ++SceneInstances;
        }
    Report->SetNumberField(TEXT("grassAllocatedInstances"), Allocated);
    Report->SetNumberField(TEXT("sceneAllocatedInstances"), SceneInstances);
    Write(Directory / TEXT("runtime.json"), Report);
}
