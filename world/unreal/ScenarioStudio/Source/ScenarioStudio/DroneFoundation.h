#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "DroneFoundation.generated.h"

// Local engineering instrumentation. No character data or browser console access.
UCLASS()
class SCENARIOSTUDIO_API ADroneFoundation : public AActor
{
    GENERATED_BODY()
  public:
    ADroneFoundation();
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type Reason) override;

  private:
    void Poll();
    FString Directory, LastRequest;
    FTimerHandle Timer;
    int32 CaptureHeight = 2160;
    TMap<FString, FVector> VehicleOrigins;
};
