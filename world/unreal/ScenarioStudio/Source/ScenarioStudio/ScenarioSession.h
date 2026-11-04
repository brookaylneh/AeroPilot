#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "TimerManager.h"
#include "ScenarioSession.generated.h"

class AScenarioManager;
class ACameraActor;
class IPixelStreaming2Streamer;
class FJsonObject;
class UDecalComponent;
class URectLightComponent;
class UMaterialInterface;
class UPCGGenSourceComponent;

namespace ScenarioNavigation
{
inline constexpr int32 ProtocolVersion = 3, Version = 10;
inline constexpr double EyeHeightCm = 170, Boost = 2, RampSeconds = .25, StopSeconds = .2, HeightSeconds = 1.2;
inline constexpr double WatchdogSeconds = 1, HitchSeconds = 2, TelemetrySeconds = .05;
inline constexpr double HeartbeatMs = 100, DefaultFov = 60, MinFov = 20, MaxFov = 90;
inline constexpr double FramingDistanceCm = 450, FlightSpeedCm = 20000;
inline constexpr double MinFlightSeconds = 2.5, MaxFlightSeconds = 6;
inline constexpr double FlightArcFraction = .1, MaxFlightArcCm = 4000;
struct Mode
{
    const TCHAR *Id, *Label, *Key;
    double SpeedCm, HeightCm;
};
inline constexpr Mode Modes[] = {
    {TEXT("walk"), TEXT("Walk"), TEXT("1"), 140, EyeHeightCm},
    {TEXT("run"), TEXT("Run"), TEXT("2"), 500, EyeHeightCm},
    {TEXT("survey"), TEXT("Survey"), TEXT("3"), 2000, 4000},
};
inline constexpr int32 DefaultMode = 1;
}

// One admitted Pixel Streaming peer edits this session. Cosys remains independent.
UCLASS()
class SCENARIOSTUDIO_API AScenarioSession : public AActor
{
    GENERATED_BODY()
  public:
    AScenarioSession();
    bool IsReady() const { return bInitialized && BoundStreamer.IsValid(); }
    void DisableGrassSource();
    void ResetSession();
    TSharedRef<FJsonObject> CameraPacket() const;
    UFUNCTION(Exec) void CameraView(const FString &View);
    UFUNCTION(Exec) void CameraTravel(double U, double V);
    UFUNCTION(Exec) void CameraNavigation(const FString &Input);
    UPROPERTY(EditAnywhere) TObjectPtr<AScenarioManager> Manager;
    UPROPERTY(EditAnywhere) TObjectPtr<UMaterialInterface> MarkerMaterial;
    virtual void PostInitializeComponents() override;
    virtual void BeginPlay() override;
    virtual void Tick(float DeltaSeconds) override;
    virtual void EndPlay(const EEndPlayReason::Type Reason) override;

  private:
    UPROPERTY() TObjectPtr<ACameraActor> Camera;
    UPROPERTY() TObjectPtr<UPCGGenSourceComponent> GrassSource;
    UPROPERTY() TObjectPtr<UDecalComponent> Marker;
    UPROPERTY() TObjectPtr<URectLightComponent> PreviewFill;
    TWeakPtr<IPixelStreaming2Streamer> BoundStreamer;
    FTimerHandle BindTimer;
    FDelegateHandle PlayerDisconnectedHandle;
    FString DroneViewPlayer;
    FVector CameraTarget = FVector::ZeroVector;
    double CameraYaw = -160, CameraPitch = 30, CameraDistance = 6000;
    double InspectionGroundClearanceCm = 100;
    double ViewportAspect = 16. / 9.;
    bool bPreviewFraming = false;
    bool bFollowPerson = false;
    FString LastSavedId;
    FString CreatorStage = TEXT("person");
    TSharedPtr<FJsonObject> Briefing;
    bool SetCreatorStage(const FString &Stage, FString &Error);
    bool CapturePortrait(FString &Error);
    bool SetBriefing(const TSharedPtr<FJsonObject> &Value, FString &Error);
    TSharedRef<FJsonObject> PublicBriefing() const;
    bool bInitialized = false;
    bool bMissionActive = false;
    bool bShowMarker = true;
    // Navigation is transient, independent of saved target authoring data.
    FString NavigationPlayer, NavigationSession;
    double NavigationSequence = -1, LastInputTime = 0, LastTelemetryTime = 0, LastNavigationTick = 0;
    double InputYaw = 0, InputPitch = 0, InputZoom = 0;
    int32 NavigationMode = ScenarioNavigation::DefaultMode;
    bool bNavigationBoost = false;
    bool bManualHeight = false;
    double NavigationAcceleration = 0;
    double NavigationClearance = ScenarioNavigation::EyeHeightCm;
    double HeightFrom = ScenarioNavigation::EyeHeightCm, HeightElapsed = ScenarioNavigation::HeightSeconds;
    double DesiredYaw = 0, DesiredPitch = 0, DesiredFieldOfView = ScenarioNavigation::DefaultFov;
    FVector NavigationAxes = FVector::ZeroVector, NavigationVelocity = FVector::ZeroVector;
    bool bFreeCamera = true;
    FTransform CameraTransitionFrom, CameraTransitionTo;
    double CameraTransitionStart = 0, CameraTransitionDuration = 0;
    float CameraTransitionFov = ScenarioNavigation::DefaultFov;
    double FlightStartClearanceCm = ScenarioNavigation::EyeHeightCm;
    bool bNavigationFlight = false;
    // The placed person stays hidden and paused until its flight ends.
    bool bPlacementFlight = false;
    FVector NavigationDestination = FVector::ZeroVector;
    bool bBriefingReturnPending = false;
    bool TransitionCamera(const FString &View, double Duration, FString &Error);
    void HandleNavigation(const FString &Player, const TSharedPtr<FJsonObject> &Input);
    void StopNavigation();
    void EnterFreeCamera();
    void FinishFlight();
    bool TravelTo(const FString &Space, double U, double V, bool Place, FString &Error);
    bool GroundAt(const FVector &Point, double &GroundZ) const;
    FVector ConstrainCamera(FVector Location, double ClearanceCm) const;
    TSharedRef<FJsonObject> NavigationSettings() const;
    void SendCameraState();
    void BindStream();
    void PlayerDisconnected(FString StreamerId, FString PlayerId);
    void HandleCommand(const FString &Player, const FString &Message);
    void UpdateCamera();
    void UpdateMarker();
    bool SetCamera(const FString &View, FString &Error);
    bool ResetScenario(FString &Error);
    bool Save(const FString &Name, const FString &Summary, FString &Error);
    bool Load(const FString &Id, FString &Error);
    TSharedRef<FJsonObject> Packet() const;
};
