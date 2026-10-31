#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ScenarioGrass.generated.h"

class UBoxComponent;
class UPCGComponent;
class UPCGGraph;
class UPCGSettings;

UCLASS()
class SCENARIOSTUDIO_API AScenarioGrass : public AActor
{
    GENERATED_BODY()
  public:
    AScenarioGrass();
    UPROPERTY(VisibleAnywhere) TObjectPtr<UBoxComponent> Bounds;
    UPROPERTY(VisibleAnywhere) TObjectPtr<UPCGComponent> Grass;
    UPROPERTY(EditAnywhere) TObjectPtr<UPCGGraph> StudioGraph;
    UPROPERTY(EditAnywhere) TObjectPtr<UPCGGraph> MissionGraph;
    UFUNCTION(BlueprintCallable, Category = "Scenario|Authoring")
    void Configure();
    UFUNCTION(BlueprintCallable, Category = "Scenario|Authoring")
    static void SetKernelSource(UPCGSettings *Settings, const FString &Source);
    void EnterMission();
    void EnterStudio();
    virtual void BeginPlay() override;
    virtual void Tick(float DeltaSeconds) override;
};
