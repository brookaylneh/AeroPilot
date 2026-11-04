#include "ScenarioCatalog.h"
#include "Components/SceneComponent.h"

const FString &FScenarioAppearance::JsonKey(FName Key)
{
    // Cooked FNames share display casing with reflected properties (TopId,
    // BottomId). Wire and persisted field names must retain their exact schema.
    static const TMap<FName, FString> Names = {
        {TEXT("personId"), TEXT("personId")},          {TEXT("topId"), TEXT("topId")},
        {TEXT("topColor"), TEXT("topColor")},          {TEXT("bottomId"), TEXT("bottomId")},
        {TEXT("bottomColor"), TEXT("bottomColor")},    {TEXT("backpackId"), TEXT("backpackId")},
        {TEXT("backpackColor"), TEXT("backpackColor")}};
    return Names.FindChecked(Key);
}

AScenarioPerson::AScenarioPerson()
{
    RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("FootOrigin"));
    Tags.Add(TEXT("ScenarioTarget"));
}
bool AScenarioPerson::ApplyAppearance_Implementation(const FScenarioAppearance &)
{
    return false;
}
