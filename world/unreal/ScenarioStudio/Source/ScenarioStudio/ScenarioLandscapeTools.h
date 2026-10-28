#pragma once
#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "ScenarioLandscapeTools.generated.h"

class UMaterialInterface;
// Editor authoring only. Never exposed through the browser protocol.
UCLASS()
class SCENARIOSTUDIO_API UScenarioLandscapeTools : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()
  public:
    UFUNCTION(BlueprintCallable, Category = "Scenario|Authoring")
    static AActor *CreateLandscape(const FString &HeightFile, int32 Resolution, double WidthCm,
                                   UMaterialInterface *Material);
};
