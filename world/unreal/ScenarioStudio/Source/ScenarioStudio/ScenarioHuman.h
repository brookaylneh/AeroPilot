#pragma once
#include "CoreMinimal.h"
#include "ScenarioCatalog.h"
#include "ScenarioHuman.generated.h"

class UChildActorComponent;
class UStaticMeshComponent;
class UAnimSequence;
class USkeletalMeshComponent;
class USkeleton;

USTRUCT(BlueprintType)
struct FScenarioTintBinding
{
    GENERATED_BODY()
    UPROPERTY(EditAnywhere, BlueprintReadWrite) FName AppearanceKey;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) FName ComponentName;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) int32 MaterialSlot = 0;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) FName ParameterName;
};

// The browser refers to catalog IDs. MetaHuman assembly details stay in this actor.
UCLASS(Blueprintable)
class SCENARIOSTUDIO_API AScenarioHuman : public AScenarioPerson
{
    GENERATED_BODY()
  public:
    UFUNCTION(BlueprintCallable, Category = "Scenario|Authoring")
    static bool PrepareStandingAnimation(UAnimSequence *Animation, USkeleton *TargetSkeleton);
    UFUNCTION(BlueprintCallable, Category = "Scenario|Authoring")
    static bool PrepareSituationAnimation(UAnimSequence *Animation, UAnimSequence *Reference,
                                          FName Situation);
    UFUNCTION(BlueprintCallable, Category = "Scenario|Authoring")
    static bool PrepareActivityAnimation(UAnimSequence *Animation, UAnimSequence *Reference, FName Activity);
    AScenarioHuman();
    UPROPERTY(EditAnywhere, BlueprintReadWrite) TSubclassOf<AActor> CharacterClass;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) TArray<FScenarioTintBinding> TintBindings;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) TMap<FName, FLinearColor> ColorPalette;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) TObjectPtr<UAnimSequence> StandingPose;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) TMap<FName, TObjectPtr<UAnimSequence>> SituationAnimations;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) FName BodyComponentName = TEXT("Body");
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly) TObjectPtr<UChildActorComponent> Character;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly) TObjectPtr<UStaticMeshComponent> Backpack;
    virtual bool ApplyAppearance_Implementation(const FScenarioAppearance &Selection) override;
    virtual bool ApplySituation(FName Situation) override;
    virtual bool ApplyMotion(bool Walking, double Time = -1) override;
    virtual double AnimationTime() const override;

  private:
    UPROPERTY(Transient) TObjectPtr<USkeletalMeshComponent> Body;
    UPROPERTY(Transient) TObjectPtr<UAnimSequence> PlayingAnimation;
    FName CurrentSituation = TEXT("lost");
};
