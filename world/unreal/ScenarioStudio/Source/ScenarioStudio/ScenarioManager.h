#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ScenarioCatalog.h"
#include "ScenarioManager.generated.h"

struct FScenarioBehavior
{
    static constexpr double HalfWidthCm = 250;
    FVector Origin = FVector::ZeroVector, Destination = FVector::ZeroVector;
    int32 Seed = 74021;
    double PauseSeconds = 0, AnimationTime = 0;
    bool bWalking = false;
};

UCLASS(Blueprintable)
class SCENARIOSTUDIO_API AScenarioManager : public AActor
{
    GENERATED_BODY()
  public:
    static constexpr double TerrainProbeRadiusCm = 1;
    AScenarioManager();
    UPROPERTY(EditAnywhere, BlueprintReadOnly) TObjectPtr<UScenarioCatalog> Catalog;
    UPROPERTY(BlueprintReadOnly) TObjectPtr<AScenarioPerson> Person;
    UPROPERTY(BlueprintReadOnly) FName RegionId;
    UPROPERTY(BlueprintReadOnly) FScenarioAppearance Appearance;
    UPROPERTY(BlueprintReadOnly) bool bPlaced = false;
    UPROPERTY(BlueprintReadOnly) FName SituationId = TEXT("lost");
    FString Difficulty = TEXT("easy");
    bool SetDifficulty(const FString &Value, FString &Error);
    bool SetSituation(FName Id, FString &Error);
    bool PreviewPerson(FString &Error);
    virtual void Tick(float DeltaSeconds) override;
    void SetBehaviorActive(bool Active);

    UFUNCTION(BlueprintCallable) bool SelectRegion(FName Id, FString &Error);
    UFUNCTION(BlueprintCallable) bool SetAppearance(const FScenarioAppearance &Selection, FString &Error);
    bool GroundFromScreen(float U, float V, FVector &Ground, FString &Error) const;
    UFUNCTION(BlueprintCallable) bool PlaceAtGround(double X, double Y, FString &Error);
    UFUNCTION(BlueprintCallable) bool SetYaw(double YawDegrees, FString &Error);
    UFUNCTION(BlueprintCallable) bool RestoreSnapshot(const FString &ScenarioJson, FString &Error);
    UFUNCTION(BlueprintCallable) FString ExportAuthoringJson() const;
    // Global settings frame only. Flight/image APIs use each vehicle's local origin.
    UFUNCTION(BlueprintCallable, meta = (DisplayName = "Unreal to Cosys global NED"))
    FVector ToCosysNed(const FVector &UnrealCm) const;

  private:
    double Yaw = 0;
    bool bBehaviorActive = false;
    FScenarioBehavior Behavior;
    void ResetBehavior();
    TSharedRef<FJsonObject> BehaviorJson() const;
    bool ReadBehavior(const TSharedPtr<FJsonObject> &Value, const FVector &Position, FScenarioBehavior &Result,
                      FString &Error) const;
    bool ValidateGround(FName Region, const FVector &Point, const FScenarioPersonPreset &Preset,
                        FName Situation, FVector &Ground, FString &Error) const;
    bool ValidateAppearance(const FScenarioAppearance &Selection, const FScenarioPersonPreset *&Preset,
                            FString &Error) const;
};
