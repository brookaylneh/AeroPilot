#pragma once
#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "GameFramework/Actor.h"
#include "ScenarioCatalog.generated.h"

USTRUCT(BlueprintType)
struct FScenarioAppearance
{
    GENERATED_BODY()
    UPROPERTY(EditAnywhere, BlueprintReadWrite) TMap<FName, FName> Options;
    static const FString &JsonKey(FName Key);
};

UCLASS(Abstract, Blueprintable)
class SCENARIOSTUDIO_API AScenarioPerson : public AActor
{
    GENERATED_BODY()
  public:
    AScenarioPerson();
    // Blueprint sets modular skeletal meshes, leader pose, sockets and material instances.
    // Must return false when any selected asset cannot be applied. Foot origin is Z=0.
    UFUNCTION(BlueprintNativeEvent, BlueprintCallable)
    bool ApplyAppearance(const FScenarioAppearance &Appearance);
    virtual bool ApplyAppearance_Implementation(const FScenarioAppearance &Appearance);
    virtual bool ApplySituation(FName Situation)
    {
        return false;
    }
    virtual bool ApplyMotion(bool Walking, double Time = -1)
        PURE_VIRTUAL(AScenarioPerson::ApplyMotion, return false;);
    virtual double AnimationTime() const PURE_VIRTUAL(AScenarioPerson::AnimationTime, return 0;);
};

USTRUCT(BlueprintType)
struct FScenarioRegion
{
    GENERATED_BODY()
    UPROPERTY(EditAnywhere, BlueprintReadOnly) FName Id;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) FText DisplayName;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) FVector Min = FVector(-5000, -5000, -10000);
    UPROPERTY(EditAnywhere, BlueprintReadOnly) FVector Max = FVector(5000, 5000, 10000);
    UPROPERTY(EditAnywhere, BlueprintReadOnly) FTransform PreviewCamera;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) bool bUsePreviewCamera = false;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) FVector PreviewTargetCm = FVector::ZeroVector;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) float MaximumSlopeDegrees = 28.0f;
};

USTRUCT(BlueprintType)
struct FScenarioPersonPreset
{
    GENERATED_BODY()
    UPROPERTY(EditAnywhere, BlueprintReadOnly) FName Id;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) FText DisplayName;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) TSoftClassPtr<AScenarioPerson> ActorClass;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) FString AgeCategory;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) FString Description;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) float HeightCm = 170;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) FName TopId = TEXT("crew-neck-shirt");
    UPROPERTY(EditAnywhere, BlueprintReadOnly) FName BottomId = TEXT("shorts");
    UPROPERTY(EditAnywhere, BlueprintReadOnly) bool bSupportsBackpack = true;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) FString FixedAccessories;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) TArray<FName> SupportedSituations;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) bool bCanWalk = false;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) bool bCanWave = false;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) float CapsuleRadiusCm = 35.0f;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) float CapsuleHalfHeightCm = 90.0f;
};

USTRUCT(BlueprintType)
struct FScenarioOptionGroup
{
    GENERATED_BODY()
    UPROPERTY(EditAnywhere, BlueprintReadOnly) FName Key;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) TArray<FName> AllowedValues;
};

UCLASS(BlueprintType)
class SCENARIOSTUDIO_API UScenarioCatalog : public UPrimaryDataAsset
{
    GENERATED_BODY()
  public:
    UPROPERTY(EditAnywhere, BlueprintReadOnly) FName EnvironmentId;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) FText DisplayName;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) FString AssetVersion;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) TArray<FScenarioRegion> Regions;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) TArray<FScenarioPersonPreset> People;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) TArray<FScenarioOptionGroup> AppearanceOptions;
    UPROPERTY(EditAnywhere, BlueprintReadOnly) FVector CosysOriginCm = FVector::ZeroVector;
};
