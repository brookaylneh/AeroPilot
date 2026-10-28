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
    static AActor *CreateLandscape(const FString &WorldFile, const FString &HeightFile,
                                   int32 ResolutionX, int32 ResolutionY,
                                   UMaterialInterface *Material);

    UFUNCTION(BlueprintCallable, Category = "Scenario|Authoring")
    static FVector TraceLandscape(const FVector &Start, const FVector &End);
};
