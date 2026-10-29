#include "DroneFoundation.h"
#include "ScenarioSession.h"
#include "EngineUtils.h"
#include "Engine/World.h"
#include "Engine/TextureRenderTarget2D.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformFileManager.h"
#include "Serialization/JsonSerializer.h"
#include "TimerManager.h"
#include "RenderTimer.h"
#include "DynamicRHI.h"
#include "ContentStreaming.h"
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
}
ADroneFoundation::ADroneFoundation()
{
    PrimaryActorTick.bCanEverTick = false;
}
void ADroneFoundation::BeginPlay()
{
    Super::BeginPlay();
    if (!FParse::Value(FCommandLine::Get(), TEXT("DroneFoundationDir="), Directory) || Directory.IsEmpty())
        return;
    Directory = FPaths::ConvertRelativePathToFull(Directory);
    IFileManager::Get().MakeDirectory(*Directory, true);
    GetWorldTimerManager().SetTimer(Timer, this, &ADroneFoundation::Poll, .5f, true, 3.f);
}
void ADroneFoundation::EndPlay(const EEndPlayReason::Type Reason)
{
    GetWorldTimerManager().ClearTimer(Timer);
    Super::EndPlay(Reason);
}
void ADroneFoundation::Poll()
{
    TActorIterator<ASimModeBase> Modes(GetWorld());
    if (!Modes)
        return;
    ASimModeBase *Mode = *Modes;
    FString Text;
    TSharedPtr<FJsonObject> Command;
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
        // Keep the stock aircraft and its camera mounts. Only front_center is used
        // as the RGB sensor. Other stock mounts remain inactive, at default size.
        // Settings cameras replace the API entry, not the pawn's original
        // embedded-camera map. Resolve through the same registry as RPC.
        APIPCamera *Camera =
            Mode->getCamera(msr::airlib::CameraDetails("front_center", TCHAR_TO_UTF8(*Name)));
        if (Camera)
        {
            // SceneCapture cameras are not player views. Register each sensor
            // with texture/landscape-grass streaming so distant drone imagery
            // does not depend on where the browser camera is looking.
            auto *Capture = Camera->getCaptureComponent(APIPCamera::ImageType::Scene, false);
            checkf(Capture, TEXT("Missing RGB capture component for %s"), *Name);
            const float Width = CaptureHeight * 16.f / 9.f;
            const float Fov = Capture->FOVAngle;
            IStreamingManager::Get().AddViewInformation(
                Camera->GetActorLocation(), Width, Width / FMath::Tan(FMath::DegreesToRadians(Fov * .5f)),
                1.f, false, .7f, nullptr, GetWorld());
            auto *Target = Camera->getRenderTarget(APIPCamera::ImageType::Scene, false);
            if (Target && Target->SizeY != CaptureHeight)
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
            if (Target)
            {
                Item->SetNumberField(TEXT("width"), Target->SizeX);
                Item->SetNumberField(TEXT("height"), Target->SizeY);
            }
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
    Write(Directory / TEXT("runtime.json"), Report);
}
