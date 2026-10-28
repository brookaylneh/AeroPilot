#include "ScenarioSession.h"
#include "ScenarioManager.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Engine/World.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "IPixelStreaming2Streamer.h"
#include "RenderTimer.h"
#include "DynamicRHI.h"

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
}

void AScenarioSession::StopNavigation()
{
    NavigationAxes = NavigationVelocity = FVector::ZeroVector;
    LastInputTime = 0;
}

void AScenarioSession::EnterFreeCamera()
{
    if (!Camera)
        return;
    bFreeCamera = true;
    DesiredYaw = Camera->GetActorRotation().Yaw;
    DesiredPitch = Camera->GetActorRotation().Pitch;
    StopNavigation();
}

FVector AScenarioSession::ConstrainCamera(FVector P) const
{
    if (!Manager || !Manager->Catalog)
        return P;
    const auto *R = Manager->Catalog->Regions.FindByPredicate(
        [this](const FScenarioRegion &Region) { return Region.Id == Manager->RegionId; });
    if (!R)
        return P;
    // Camera access to the compact southern port does not extend placement bounds.
    const bool PortApproach =
        Manager->Catalog->EnvironmentId == TEXT("open-meadow-unreal") && FMath::Abs(P.Y) < 5000;
    P.X = FMath::Clamp(P.X, R->Min.X + (PortApproach ? -6000 : 100), R->Max.X - 100);
    P.Y = FMath::Clamp(P.Y, R->Min.Y + 100, R->Max.Y - 100);
    FHitResult Hit;
    FCollisionQueryParams Query;
    if (GetWorld()->LineTraceSingleByChannel(Hit, FVector(P.X, P.Y, R->Max.Z), FVector(P.X, P.Y, R->Min.Z),
                                             P.X < R->Min.X ? ECC_Visibility : ECC_GameTraceChannel1, Query))
        P.Z = FMath::Clamp(P.Z, Hit.ImpactPoint.Z + 170, Hit.ImpactPoint.Z + 300000);
    return P;
}

bool AScenarioSession::TravelMap(double U, double V, FString &Error)
{
    if (!FMath::IsFinite(U) || !FMath::IsFinite(V) || U < 0 || U > 1 || V < 0 || V > 1 || !Camera)
    {
        Error = TEXT("Invalid map coordinate");
        return false;
    }
    const auto *R = Manager->Catalog->Regions.FindByPredicate(
        [this](const FScenarioRegion &Region) { return Region.Id == Manager->RegionId; });
    if (!R)
    {
        Error = TEXT("Unknown map region");
        return false;
    }
    // North is Unreal +X; east is +Y, matching the documented Cosys NED frame.
    // Preserve the map's full-world scale; only clamp points within 1m of an edge.
    FVector P(FMath::Clamp(FMath::Lerp(R->Max.X, R->Min.X, V), R->Min.X + 100, R->Max.X - 100),
              FMath::Clamp(FMath::Lerp(R->Min.Y, R->Max.Y, U), R->Min.Y + 100, R->Max.Y - 100), 0);
    const FVector Origin = Camera->GetActorLocation();
    // Outside-world inspection views use the nearest terrain edge as their height reference.
    const auto GroundAt = [this, R](const FVector &Point, FHitResult &Hit) {
        const double X = FMath::Clamp(Point.X, R->Min.X + 100, R->Max.X - 100);
        const double Y = FMath::Clamp(Point.Y, R->Min.Y + 100, R->Max.Y - 100);
        FCollisionQueryParams Query;
        return GetWorld()->LineTraceSingleByChannel(Hit, FVector(X, Y, R->Max.Z), FVector(X, Y, R->Min.Z),
                                                    ECC_GameTraceChannel1, Query);
    };
    FHitResult OriginGround, DestinationGround;
    if (!GroundAt(Origin, OriginGround) || !GroundAt(P, DestinationGround))
    {
        Error = TEXT("Map travel requires terrain at both locations");
        return false;
    }
    P.Z = DestinationGround.ImpactPoint.Z + (Origin.Z - OriginGround.ImpactPoint.Z);
    Camera->SetActorLocation(P);
    EnterFreeCamera();
    return true;
}

void AScenarioSession::HandleNavigation(const FString &Player, const TSharedPtr<FJsonObject> &Input)
{
    // Small, explicit input-only protocol. No replies, disk reads or actor spawning.
    if (!Camera || !Input || Input->Values.Num() != 11)
        return;
    FString Session;
    FGuid Guid;
    double Protocol, Sequence, Forward, Right, Up, Yaw, Pitch, Zoom, Speed;
    if (!Input->TryGetNumberField(TEXT("protocolVersion"), Protocol) || Protocol != 1 ||
        !Input->TryGetStringField(TEXT("session"), Session) ||
        !FGuid::ParseExact(Session, EGuidFormats::DigitsWithHyphens, Guid) ||
        !Input->TryGetNumberField(TEXT("sequence"), Sequence) ||
        !Input->TryGetNumberField(TEXT("forward"), Forward) ||
        !Input->TryGetNumberField(TEXT("right"), Right) || !Input->TryGetNumberField(TEXT("up"), Up) ||
        !Input->TryGetNumberField(TEXT("yaw"), Yaw) || !Input->TryGetNumberField(TEXT("pitch"), Pitch) ||
        !Input->TryGetNumberField(TEXT("speed"), Speed) || !Input->TryGetNumberField(TEXT("zoom"), Zoom))
        return;
    for (double V : {Sequence, Forward, Right, Up, Yaw, Pitch, Zoom, Speed})
        if (!FMath::IsFinite(V))
            return;
    if (Sequence < 0 || Sequence > 9e12 || FMath::FloorToDouble(Sequence) != Sequence ||
        FMath::Abs(Forward) > 1 || FMath::Abs(Right) > 1 || FMath::Abs(Up) > 1 || Speed < 140 ||
        Speed > 3000 || FMath::Abs(Yaw) > 1e9 || FMath::Abs(Pitch) > 1e9 || FMath::Abs(Zoom) > 1e9)
        return;
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
    if (!bFreeCamera && (Forward != 0 || Right != 0 || Up != 0))
        EnterFreeCamera();
    DesiredYaw = FRotator::NormalizeAxis(DesiredYaw + FMath::Clamp(Yaw - InputYaw, -60., 60.));
    DesiredPitch = FMath::Clamp(DesiredPitch + FMath::Clamp(Pitch - InputPitch, -60., 60.),
                                bFreeCamera ? -85. : -80., bFreeCamera ? 85. : 5.);
    DesiredFieldOfView =
        FMath::Clamp(DesiredFieldOfView * FMath::Exp(FMath::Clamp(Zoom - InputZoom, -.25, .25)), 20., 90.);
    InputYaw = Yaw;
    InputPitch = Pitch;
    InputZoom = Zoom;
    NavigationAxes = FVector(Forward, Right, Up).GetClampedToMaxSize(1.0);
    // Key release stops immediately; no drift after blur, disconnect or Escape.
    if (NavigationAxes.IsNearlyZero())
        NavigationVelocity = FVector::ZeroVector;
    NavigationSpeed = Speed;
    LastInputTime = FPlatformTime::Seconds();
}

void AScenarioSession::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (!bInitialized || !Camera)
        return;
    const double Now = FPlatformTime::Seconds();
    if (Now - LastInputTime > .25)
        StopNavigation();
    const float Dt = FMath::Min(DeltaSeconds, .05f);
    if (CameraTransitionDuration > 0)
    {
        const double T = FMath::Clamp((Now - CameraTransitionStart) / CameraTransitionDuration, 0., 1.);
        const double Alpha = T * T * (3. - 2. * T);
        const FVector Start = CameraTransitionFrom.GetLocation(), End = CameraTransitionTo.GetLocation();
        FVector Position = FMath::Lerp(Start, End, Alpha);
        // Clear intervening hills while approaching the person for the briefing transition.
        Position.Z += 4. * Alpha * (1. - Alpha) * FMath::Min(FVector::Dist2D(Start, End) * .35, 50000.);
        const FQuat Rotation =
            FQuat::Slerp(CameraTransitionFrom.GetRotation(), CameraTransitionTo.GetRotation(), Alpha);
        Camera->SetActorLocationAndRotation(Position, Rotation);
        Camera->GetCameraComponent()->SetFieldOfView(
            FMath::Lerp(CameraTransitionFov, static_cast<float>(DesiredFieldOfView), Alpha));
        if (T >= 1.)
        {
            CameraTransitionDuration = 0;
            if (bBriefingRisePending)
            {
                FString Error;
                const bool Started = TransitionCamera(TEXT("departure"), 2.4, Error);
                checkf(Started, TEXT("Cannot start briefing camera: %s"), *Error);
            }
        }
    }
    else
    {
        const FRotator Current = Camera->GetActorRotation();
        // Look packets arrive unevenly; a softer rate spreads each step over frames.
        const FRotator Rotation = FMath::RInterpTo(Current, FRotator(DesiredPitch, DesiredYaw, 0), Dt, 15.f);
        auto *Lens = Camera->GetCameraComponent();
        Lens->SetFieldOfView(FMath::FInterpTo(Lens->FieldOfView, DesiredFieldOfView, Dt, 18.f));
        if (bFreeCamera)
        {
            Camera->SetActorRotation(Rotation);
            // Keyboard travel stays level regardless of where the camera looks.
            // Mouse-wheel zoom changes only the lens; Q/E controls height.
            const FRotationMatrix Heading(FRotator(0, Rotation.Yaw, 0));
            const FVector Wanted =
                (Heading.GetUnitAxis(EAxis::X) * NavigationAxes.X +
                 Heading.GetUnitAxis(EAxis::Y) * NavigationAxes.Y + FVector::UpVector * NavigationAxes.Z)
                    .GetClampedToMaxSize(1.) *
                NavigationSpeed;
            // A ~0.4 s ramp: an instant full-frame motion onset is the worst case for the encoder.
            NavigationVelocity = FMath::VInterpTo(NavigationVelocity, Wanted, Dt, 5.f);
            if (!NavigationVelocity.IsNearlyZero())
                Camera->SetActorLocation(
                    ConstrainCamera(Camera->GetActorLocation() + NavigationVelocity * Dt));
        }
        else
        {
            CameraYaw = Rotation.Yaw - 180.;
            CameraPitch = -Rotation.Pitch;
            UpdateCamera();
        }
    }
    if (Now - LastTelemetryTime >= .1)
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
    State->SetBoolField(TEXT("transitioning"), CameraTransitionDuration > 0);
    State->SetNumberField(TEXT("inputSequence"), NavigationSequence);
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
    Reply->SetNumberField(TEXT("protocolVersion"), 1);
    Reply->SetStringField(TEXT("type"), TEXT("navigation-state"));
    Reply->SetObjectField(TEXT("camera"), CameraPacket());
    FString Text;
    FJsonSerializer::Serialize(Reply,
                               TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Text));
    Streamer->SendPlayerMessage(NavigationPlayer, TEXT("Response"), Text);
}
