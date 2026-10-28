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

// One admitted Pixel Streaming peer edits this session. Cosys remains independent.
UCLASS()
class SCENARIOSTUDIO_API AScenarioSession : public AActor
{
    GENERATED_BODY()
  public:
    AScenarioSession();
    UPROPERTY(EditAnywhere) TObjectPtr<AScenarioManager> Manager;
    UPROPERTY(EditAnywhere) TObjectPtr<UMaterialInterface> MarkerMaterial;
    virtual void BeginPlay() override;
    virtual void Tick(float DeltaSeconds) override;
    virtual void EndPlay(const EEndPlayReason::Type Reason) override;

  private:
    UPROPERTY() TObjectPtr<ACameraActor> Camera;
    UPROPERTY() TObjectPtr<UDecalComponent> Marker;
    UPROPERTY() TObjectPtr<URectLightComponent> PreviewFill;
    TWeakPtr<IPixelStreaming2Streamer> BoundStreamer;
    FTimerHandle BindTimer;
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
    bool bShowMarker = true;
    // Navigation is transient, independent of saved target authoring data.
    FString NavigationPlayer, NavigationSession;
    double NavigationSequence = -1, LastInputTime = 0, LastTelemetryTime = 0;
    double InputYaw = 0, InputPitch = 0, InputZoom = 0;
    double NavigationSpeed = 300, DesiredYaw = 0, DesiredPitch = 0, DesiredFieldOfView = 60;
    FVector NavigationAxes = FVector::ZeroVector, NavigationVelocity = FVector::ZeroVector;
    bool bFreeCamera = true;
    FTransform CameraTransitionFrom, CameraTransitionTo;
    double CameraTransitionStart = 0, CameraTransitionDuration = 0;
    float CameraTransitionFov = 60;
    bool bBriefingRisePending = false;
    bool TransitionCamera(const FString &View, double Duration, FString &Error);
    void HandleNavigation(const FString &Player, const TSharedPtr<FJsonObject> &Input);
    void StopNavigation();
    void EnterFreeCamera();
    bool TravelMap(double U, double V, FString &Error);
    FVector ConstrainCamera(FVector Location) const;
    TSharedRef<FJsonObject> CameraPacket() const;
    void SendCameraState();
    void BindStream();
    void HandleCommand(const FString &Player, const FString &Message);
    void UpdateCamera();
    void UpdateMarker();
    bool SetCamera(const FString &View, FString &Error);
    bool ResetScenario(FString &Error);
    bool Save(const FString &Name, const FString &Summary, FString &Error);
    bool Load(const FString &Id, FString &Error);
    TSharedRef<FJsonObject> Packet() const;
};
