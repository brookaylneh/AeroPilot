#include "ScenarioSession.h"
#include "ScenarioManager.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/RectLightComponent.h"
#include "Engine/World.h"
#include "CollisionShape.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "IPixelStreaming2Streamer.h"
#include "RenderTimer.h"
#include "DynamicRHI.h"

namespace Nav = ScenarioNavigation;
namespace
{
TSharedRef<FJsonObject> VectorJson(const FVector &P)
{
    auto R = MakeShared<FJsonObject>();
    R->SetNumberField(TEXT("x"), P.X);
    R->SetNumberField(TEXT("y"), P.Y);
    R->SetNumberField(TEXT("z"), P.Z);
    return R;
}
double Smoothstep(double T)
{
    T = FMath::Clamp(T, 0., 1.);
    return T * T * (3. - 2. * T);
}
double CameraEase(double T)
{
    T = FMath::Clamp(T, 0., 1.);
    return T * T * T * (T * (T * 6. - 15.) + 10.);
}
}

TSharedRef<FJsonObject> AScenarioSession::NavigationSettings() const
{
    auto Settings = MakeShared<FJsonObject>();
    Settings->SetNumberField(TEXT("heartbeatMs"), Nav::HeartbeatMs);
    Settings->SetNumberField(TEXT("boost"), Nav::Boost);
    TArray<TSharedPtr<FJsonValue>> Modes;
    for (const auto &Mode : Nav::Modes)
    {
        auto Item = MakeShared<FJsonObject>();
        Item->SetStringField(TEXT("id"), Mode.Id);
        Item->SetStringField(TEXT("label"), Mode.Label);
        Item->SetStringField(TEXT("key"), Mode.Key);
        Item->SetNumberField(TEXT("speedCm"), Mode.SpeedCm);
        Item->SetNumberField(TEXT("heightCm"), Mode.HeightCm);
        Modes.Add(MakeShared<FJsonValueObject>(Item));
    }
    Settings->SetArrayField(TEXT("modes"), Modes);
    return Settings;
}

void AScenarioSession::StopNavigation()
{
    NavigationAxes = NavigationVelocity = FVector::ZeroVector;
    LastInputTime = 0;
}

void AScenarioSession::EnterFreeCamera()
{
    check(Camera);
    bFreeCamera = true;
    DesiredYaw = Camera->GetActorRotation().Yaw;
    DesiredPitch = Camera->GetActorRotation().Pitch;
    double GroundZ;
    checkf(GroundAt(Camera->GetActorLocation(), GroundZ), TEXT("Exploration requires terrain"));
    HeightFrom = NavigationClearance = Camera->GetActorLocation().Z - GroundZ;
    HeightElapsed = 0;
    StopNavigation();
}

bool AScenarioSession::GroundAt(const FVector &Point, double &GroundZ) const
{
    check(Manager && Manager->Catalog);
    const auto *R = Manager->Catalog->Regions.FindByPredicate(
        [this](const FScenarioRegion &Region) { return Region.Id == Manager->RegionId; });
    check(R);
    const double X = FMath::Clamp(Point.X, R->Min.X + 100, R->Max.X - 100);
    const double Y = FMath::Clamp(Point.Y, R->Min.Y + 100, R->Max.Y - 100);
    FHitResult Hit;
    const FCollisionQueryParams Query(SCENE_QUERY_STAT(ScenarioNavigationGround), true);
    // A finite footprint avoids heightfield ray misses on shared triangle edges.
    if (!GetWorld()->SweepSingleByChannel(Hit, FVector(X, Y, R->Max.Z), FVector(X, Y, R->Min.Z),
                                         FQuat::Identity, ECC_GameTraceChannel1,
                                         FCollisionShape::MakeSphere(AScenarioManager::TerrainProbeRadiusCm), Query))
        return false;
    GroundZ = Hit.ImpactPoint.Z;
    return true;
}

FVector AScenarioSession::ConstrainCamera(FVector P, double ClearanceCm) const
{
    const auto *R = Manager->Catalog->Regions.FindByPredicate(
        [this](const FScenarioRegion &Region) { return Region.Id == Manager->RegionId; });
    check(R);
    // Camera access to the compact southern port does not extend placement bounds.
    const bool PortApproach =
        Manager->Catalog->EnvironmentId == TEXT("open-meadow-unreal") && FMath::Abs(P.Y) < 5000;
    P.X = FMath::Clamp(P.X, R->Min.X + (PortApproach ? -6000 : 100), R->Max.X - 100);
    P.Y = FMath::Clamp(P.Y, R->Min.Y + 100, R->Max.Y - 100);
    double GroundZ;
    checkf(GroundAt(P, GroundZ), TEXT("Camera left the terrain"));
    P.Z = FMath::Max(P.Z, GroundZ + ClearanceCm);
    return P;
}

void AScenarioSession::FinishFlight()
{
    if (bPlacementFlight)
    {
        Manager->Person->SetActorHiddenInGame(false);
        Manager->SetBehaviorActive(true);
    }
    bNavigationFlight = bPlacementFlight = false;
}

bool AScenarioSession::TravelTo(const FString &Space, double U, double V, bool Place, FString &Error)
{
    if ((Place && CreatorStage != TEXT("placement")) || !FMath::IsFinite(U) || !FMath::IsFinite(V) || U < 0 ||
        U > 1 || V < 0 || V > 1 || !Camera)
    {
        Error = TEXT("Placement requires the placement stage; travel requires normalized coordinates");
        return false;
    }
    const auto *R = Manager->Catalog->Regions.FindByPredicate(
        [this](const FScenarioRegion &Region) { return Region.Id == Manager->RegionId; });
    check(R);
    FVector Ground;
    if (Space == TEXT("screen"))
    {
        if (!Manager->GroundFromScreen(U, V, Ground, Error))
            return false;
    }
    else if (Space == TEXT("map"))
    {
        // North is Unreal +X; east is +Y. The map retains its full-world extent.
        Ground = FVector(FMath::Clamp(FMath::Lerp(R->Max.X, R->Min.X, V), R->Min.X + 100, R->Max.X - 100),
                         FMath::Clamp(FMath::Lerp(R->Min.Y, R->Max.Y, U), R->Min.Y + 100, R->Max.Y - 100), 0);
        if (!GroundAt(Ground, Ground.Z))
        {
            Error = FString::Printf(TEXT("The destination has no terrain at %.3f, %.3f cm"), Ground.X, Ground.Y);
            return false;
        }
    }
    else
    {
        Error = TEXT("Unknown travel coordinate space");
        return false;
    }
    const double Yaw = Camera->GetActorRotation().Yaw;
    FVector End = Ground - FRotator(0, Yaw, 0).Vector() * Nav::FramingDistanceCm;
    if (End.X < R->Min.X + 100 || End.X > R->Max.X - 100 || End.Y < R->Min.Y + 100 ||
        End.Y > R->Max.Y - 100 || !GroundAt(End, End.Z))
    {
        Error = TEXT("The destination needs terrain for the approach camera");
        return false;
    }
    End.Z += Nav::EyeHeightCm;
    double StartGroundZ;
    if (!GroundAt(Camera->GetActorLocation(), StartGroundZ))
    {
        Error = TEXT("The approach camera has no terrain beneath it");
        return false;
    }
    if (Place && !Manager->PlaceAtGround(Ground.X, Ground.Y, Error))
        return false;
    FinishFlight();
    if (Place)
    {
        Manager->SetBehaviorActive(false);
        Manager->Person->SetActorHiddenInGame(true);
    }
    StopNavigation();
    bFreeCamera = true;
    bPreviewFraming = false;
    PreviewFill->SetVisibility(false);
    bFollowPerson = false;
    bBriefingReturnPending = false;
    bNavigationFlight = true;
    bPlacementFlight = Place;
    NavigationMode = Nav::DefaultMode;
    bNavigationBoost = false;
    bManualHeight = false;
    NavigationDestination = Ground;
    CameraTransitionFrom = Camera->GetActorTransform();
    FlightStartClearanceCm = FMath::Min(CameraTransitionFrom.GetLocation().Z - StartGroundZ, Nav::EyeHeightCm);
    const FRotator Rotation = (Ground + FVector(0, 0, Nav::EyeHeightCm * .5) - End).Rotation();
    CameraTransitionTo = FTransform(Rotation, End);
    CameraTransitionFov = Camera->GetCameraComponent()->FieldOfView;
    DesiredFieldOfView = Nav::DefaultFov;
    DesiredYaw = Yaw;
    DesiredPitch = Rotation.Pitch;
    CameraTransitionStart = FPlatformTime::Seconds();
    CameraTransitionDuration = FMath::Clamp(FVector::Dist2D(CameraTransitionFrom.GetLocation(), End) /
                                               Nav::FlightSpeedCm,
                                           Nav::MinFlightSeconds, Nav::MaxFlightSeconds);
    return true;
}

void AScenarioSession::HandleNavigation(const FString &Player, const TSharedPtr<FJsonObject> &Input)
{
    if (bMissionActive)
        return;
    const auto Reject = [] { UE_LOG(LogTemp, Error, TEXT("Rejected malformed navigation input")); };
    if (!Camera || !Input || Input->Values.Num() != 12)
    {
        Reject();
        return;
    }
    FString Session, Mode;
    FGuid Guid;
    bool Boost;
    double Protocol, Sequence, Forward, Right, Up, Yaw, Pitch, Zoom;
    if (!Input->TryGetNumberField(TEXT("protocolVersion"), Protocol) || Protocol != Nav::ProtocolVersion ||
        !Input->TryGetStringField(TEXT("session"), Session) ||
        !FGuid::ParseExact(Session, EGuidFormats::DigitsWithHyphens, Guid) ||
        !Input->TryGetNumberField(TEXT("sequence"), Sequence) ||
        !Input->TryGetNumberField(TEXT("forward"), Forward) ||
        !Input->TryGetNumberField(TEXT("right"), Right) || !Input->TryGetNumberField(TEXT("up"), Up) ||
        !Input->TryGetNumberField(TEXT("yaw"), Yaw) ||
        !Input->TryGetNumberField(TEXT("pitch"), Pitch) || !Input->TryGetNumberField(TEXT("zoom"), Zoom) ||
        !Input->TryGetStringField(TEXT("mode"), Mode) || !Input->TryGetBoolField(TEXT("boost"), Boost))
    {
        Reject();
        return;
    }
    for (double V : {Sequence, Forward, Right, Up, Yaw, Pitch, Zoom})
        if (!FMath::IsFinite(V))
        {
            Reject();
            return;
        }
    int32 ModeIndex = INDEX_NONE;
    for (int32 I = 0; I < UE_ARRAY_COUNT(Nav::Modes); ++I)
        if (Mode == Nav::Modes[I].Id)
            ModeIndex = I;
    if (ModeIndex == INDEX_NONE || Sequence < 0 || Sequence > 9e12 || FMath::FloorToDouble(Sequence) != Sequence ||
        FMath::Abs(Forward) > 1 || FMath::FloorToDouble(Forward) != Forward || FMath::Abs(Right) > 1 ||
        FMath::FloorToDouble(Right) != Right || FMath::Abs(Up) > 1 || FMath::FloorToDouble(Up) != Up ||
        FMath::Abs(Yaw) > 1e9 || FMath::Abs(Pitch) > 1e9 ||
        FMath::Abs(Zoom) > 1e9)
    {
        Reject();
        return;
    }
    if (NavigationSession != Session)
    {
        NavigationSession = Session;
        NavigationSequence = -1;
        InputYaw = InputPitch = InputZoom = 0;
        StopNavigation();
    }
    if (Sequence <= NavigationSequence)
        return;
    NavigationSequence = Sequence;
    NavigationPlayer = Player;
    const bool ModeChanged = ModeIndex != NavigationMode;
    const bool Active = Forward != 0 || Right != 0 || Up != 0 || Yaw != InputYaw || Pitch != InputPitch ||
                        Zoom != InputZoom || ModeChanged;
    if (CameraTransitionDuration > 0 && Active)
    {
        CameraTransitionDuration = 0;
        FinishFlight();
        bBriefingReturnPending = false;
        EnterFreeCamera();
    }
    if (!bFreeCamera && (Forward != 0 || Right != 0 || Up != 0))
        EnterFreeCamera();
    if (ModeChanged)
    {
        HeightFrom = NavigationClearance;
        HeightElapsed = 0;
        bManualHeight = false;
    }
    if (Up != 0)
        bManualHeight = true;
    const FVector Axes = FVector(Forward, Right, Up).GetClampedToMaxSize(1.);
    const bool AxesChanged = NavigationAxes != Axes;
    const bool MovementChanged = ModeChanged || bNavigationBoost != Boost || AxesChanged;
    NavigationMode = ModeIndex;
    bNavigationBoost = Boost;
    DesiredYaw = FRotator::NormalizeAxis(DesiredYaw + Yaw - InputYaw);
    DesiredPitch = FMath::Clamp(DesiredPitch + Pitch - InputPitch, bFreeCamera ? -85. : -80.,
                                bFreeCamera ? 85. : 5.);
    DesiredFieldOfView = FMath::Exp(FMath::Clamp(FMath::Loge(DesiredFieldOfView) + Zoom - InputZoom,
                                                FMath::Loge(Nav::MinFov), FMath::Loge(Nav::MaxFov)));
    InputYaw = Yaw;
    InputPitch = Pitch;
    InputZoom = Zoom;
    NavigationAxes = Axes;
    const double Speed = Nav::Modes[NavigationMode].SpeedCm * (Boost ? Nav::Boost : 1);
    const bool Stopping = Axes.IsNearlyZero();
    if (MovementChanged && (!Stopping || AxesChanged))
        NavigationAcceleration = FMath::Max(NavigationVelocity.Size(), Speed) /
                                 (Stopping ? Nav::StopSeconds : Nav::RampSeconds);
    LastInputTime = FPlatformTime::Seconds();
    SendCameraState();
}

void AScenarioSession::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (!bInitialized || !Camera)
        return;
    const double Now = FPlatformTime::Seconds();
    const double Dt = LastNavigationTick == 0 ? 0 : FMath::Min(Now - LastNavigationTick, Nav::HitchSeconds);
    LastNavigationTick = Now;
    check(Dt >= 0);
    if (Now - LastInputTime > Nav::WatchdogSeconds)
        StopNavigation();
    bool Stopped = false;
    if (CameraTransitionDuration > 0)
    {
        const double T = FMath::Clamp((Now - CameraTransitionStart) / CameraTransitionDuration, 0., 1.);
        const double Alpha = CameraEase(T);
        const FVector Start = CameraTransitionFrom.GetLocation(), End = CameraTransitionTo.GetLocation();
        FVector Position = FMath::Lerp(Start, End, Alpha);
        Position.Z += 4. * Alpha * (1. - Alpha) *
                      FMath::Min(FVector::Dist2D(Start, End) * Nav::FlightArcFraction, Nav::MaxFlightArcCm);
        if (bNavigationFlight)
            Position = ConstrainCamera(Position, FMath::Lerp(FlightStartClearanceCm, Nav::EyeHeightCm, Alpha));
        const FQuat Rotation =
            FQuat::Slerp(CameraTransitionFrom.GetRotation(), CameraTransitionTo.GetRotation(), Alpha);
        Camera->SetActorLocationAndRotation(Position, Rotation);
        Camera->GetCameraComponent()->SetFieldOfView(
            FMath::Lerp(CameraTransitionFov, static_cast<float>(DesiredFieldOfView), Alpha));
        if (T >= 1.)
        {
            CameraTransitionDuration = 0;
            if (bNavigationFlight)
            {
                FinishFlight();
                HeightFrom = NavigationClearance = Nav::EyeHeightCm;
                HeightElapsed = Nav::HeightSeconds;
            }
            if (bBriefingReturnPending)
            {
                FString Error;
                const bool Started = TransitionCamera(TEXT("start"), 4, Error);
                checkf(Started, TEXT("Cannot start briefing camera: %s"), *Error);
            }
        }
    }
    else
    {
        const FRotator Current = Camera->GetActorRotation();
        const FRotator Rotation = FMath::Lerp(Current, FRotator(DesiredPitch, DesiredYaw, 0),
                                              1. - FMath::Exp(-15. * Dt));
        auto *Lens = Camera->GetCameraComponent();
        Lens->SetFieldOfView(FMath::Lerp(Lens->FieldOfView, DesiredFieldOfView, 1. - FMath::Exp(-18. * Dt)));
        if (bFreeCamera)
        {
            Camera->SetActorRotation(Rotation);
            const FRotationMatrix Heading(FRotator(0, Rotation.Yaw, 0));
            const FVector Wanted = (Heading.GetUnitAxis(EAxis::X) * NavigationAxes.X +
                                     Heading.GetUnitAxis(EAxis::Y) * NavigationAxes.Y +
                                     FVector::UpVector * NavigationAxes.Z) *
                                    Nav::Modes[NavigationMode].SpeedCm * (bNavigationBoost ? Nav::Boost : 1);
            const FVector Previous = NavigationVelocity;
            const double Distance = FVector::Dist(Previous, Wanted);
            double RampDt = 0;
            if (Distance > 0)
            {
                check(NavigationAcceleration > 0);
                RampDt = FMath::Min(Dt, Distance / NavigationAcceleration);
                NavigationVelocity = Previous + (Wanted - Previous) * (RampDt * NavigationAcceleration / Distance);
                if (RampDt < Dt)
                    NavigationVelocity = Wanted;
            }
            // Integrate the ramp and the remaining constant-speed time separately.
            const FVector Displacement = (Previous + NavigationVelocity) * (.5 * RampDt) +
                                         Wanted * (Dt - RampDt);
            FVector P = Camera->GetActorLocation() + Displacement;
            Stopped = !Previous.IsNearlyZero() && NavigationVelocity.IsNearlyZero();
            P = ConstrainCamera(P, Nav::EyeHeightCm);
            double GroundZ;
            checkf(GroundAt(P, GroundZ), TEXT("Movement requires terrain"));
            if (bManualHeight)
                NavigationClearance = FMath::Max(Nav::EyeHeightCm, NavigationClearance + Displacement.Z);
            else
            {
                HeightElapsed = FMath::Min(HeightElapsed + Dt, Nav::HeightSeconds);
                NavigationClearance = FMath::Lerp(HeightFrom, Nav::Modes[NavigationMode].HeightCm,
                                                  Smoothstep(HeightElapsed / Nav::HeightSeconds));
            }
            P.Z = GroundZ + FMath::Max(Nav::EyeHeightCm, NavigationClearance);
            Camera->SetActorLocation(P);
        }
        else
        {
            CameraYaw = Rotation.Yaw - 180.;
            CameraPitch = -Rotation.Pitch;
            UpdateCamera();
        }
    }
    if (Stopped || Now - LastTelemetryTime >= Nav::TelemetrySeconds)
    {
        LastTelemetryTime = Now;
        SendCameraState();
    }
}

TSharedRef<FJsonObject> AScenarioSession::CameraPacket() const
{
    check(Camera && Camera->GetCameraComponent());
    auto State = MakeShared<FJsonObject>();
    State->SetObjectField(TEXT("position"), VectorJson(Camera->GetActorLocation()));
    State->SetNumberField(TEXT("yaw"), Camera->GetActorRotation().Yaw);
    State->SetNumberField(TEXT("pitch"), Camera->GetActorRotation().Pitch);
    State->SetNumberField(TEXT("fieldOfView"), Camera->GetCameraComponent()->FieldOfView);
    State->SetStringField(TEXT("mode"), bFreeCamera ? TEXT("explore") : TEXT("inspect"));
    State->SetStringField(TEXT("movementMode"), Nav::Modes[NavigationMode].Id);
    State->SetNumberField(TEXT("speedCmPerSecond"), NavigationVelocity.Size2D());
    State->SetNumberField(TEXT("serverTimeMs"), FPlatformTime::Seconds() * 1000.);
    double GroundZ;
    checkf(GroundAt(Camera->GetActorLocation(), GroundZ), TEXT("Camera telemetry requires terrain"));
    State->SetNumberField(TEXT("groundZ"), GroundZ);
    State->SetBoolField(TEXT("transitioning"), CameraTransitionDuration > 0);
    if (bNavigationFlight)
        State->SetObjectField(TEXT("destination"), VectorJson(NavigationDestination));
    else
        State->SetField(TEXT("destination"), MakeShared<FJsonValueNull>());
    State->SetNumberField(TEXT("inputSequence"), NavigationSequence);
    State->SetStringField(TEXT("inputSession"), NavigationSession);
    auto Stats = MakeShared<FJsonObject>();
    Stats->SetNumberField(TEXT("frameMs"), GetWorld()->GetDeltaSeconds() * 1000.);
    Stats->SetNumberField(TEXT("gameMs"), FPlatformTime::ToMilliseconds(GGameThreadTime));
    Stats->SetNumberField(TEXT("renderMs"), FPlatformTime::ToMilliseconds(GRenderThreadTime));
    Stats->SetNumberField(TEXT("rhiMs"), FPlatformTime::ToMilliseconds(GRHIThreadTime));
    Stats->SetNumberField(TEXT("gpuMs"), FPlatformTime::ToMilliseconds(RHIGetGPUFrameCycles()));
    State->SetObjectField(TEXT("timing"), Stats);
    return State;
}

void AScenarioSession::SendCameraState()
{
    auto Streamer = BoundStreamer.Pin();
    if (!Streamer || NavigationPlayer.IsEmpty())
        return;
    auto Reply = MakeShared<FJsonObject>();
    Reply->SetNumberField(TEXT("protocolVersion"), Nav::ProtocolVersion);
    Reply->SetStringField(TEXT("type"), TEXT("navigation-state"));
    Reply->SetObjectField(TEXT("camera"), CameraPacket());
    FString Text;
    FJsonSerializer::Serialize(Reply,
                               TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Text));
    Streamer->SendPlayerMessage(NavigationPlayer, TEXT("Response"), Text);
}
