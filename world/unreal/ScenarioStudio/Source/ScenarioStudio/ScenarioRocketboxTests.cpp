#if WITH_DEV_AUTOMATION_TESTS && WITH_EDITOR
#include "ScenarioHuman.h"
#include "Editor.h"
#include "Engine/World.h"
#include "Misc/AutomationTest.h"

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRocketboxRuntimeTest, "SNR.Rocketbox.RuntimeActors",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRocketboxRuntimeTest::RunTest(const FString &Parameters)
{
    auto *Catalog = LoadObject<UScenarioCatalog>(nullptr, TEXT("/Game/OpenMeadow/Catalog.Catalog"));
    if (!TestNotNull(TEXT("Catalog loads"), Catalog))
        return false;
    TestEqual(TEXT("Approved roster size"), Catalog->People.Num(), 11);
    UWorld *World = GEditor->GetEditorWorldContext().World();
    if (!TestNotNull(TEXT("Editor world exists"), World))
        return false;
    for (const auto &Preset : Catalog->People)
    {
        FScenarioAppearance Appearance;
        for (const auto &Group : Catalog->AppearanceOptions)
        {
            if (!TestTrue(TEXT("Required option has values"), !Group.AllowedValues.IsEmpty()))
                return false;
            Appearance.Options.Add(Group.Key, Group.AllowedValues[0]);
        }
        Appearance.Options.Add(TEXT("personId"), Preset.Id);
        Appearance.Options[TEXT("topId")] = Preset.TopId;
        Appearance.Options[TEXT("bottomId")] = Preset.BottomId;
        UClass *Class = Preset.ActorClass.LoadSynchronous();
        if (!TestNotNull(Preset.Id.ToString() + TEXT(" Blueprint loads"), Class))
            return false;
        auto *Actor = World->SpawnActor<AScenarioHuman>(Class);
        if (!TestNotNull(Preset.Id.ToString() + TEXT(" spawns"), Actor))
            return false;
        TestTrue(Preset.Id.ToString() + TEXT(" original appearance"), Actor->ApplyAppearance(Appearance));
        for (FName Situation : Preset.SupportedSituations)
        {
            TestTrue(Situation.ToString(), Actor->ApplySituation(Situation));
            TestTrue(TEXT("Animation time restores"), Actor->ApplyMotion(false, .5));
            TestTrue(TEXT("Animation is evaluated"), FMath::IsNearlyEqual(Actor->AnimationTime(), .5, .001));
        }
        TestTrue(TEXT("Walking starts"), Actor->ApplyMotion(true, .25));
        TestFalse(TEXT("Unknown situation rejected"), Actor->ApplySituation(TEXT("invalid")));
        Appearance.Options[TEXT("topColor")] = TEXT("rust");
        TestFalse(TEXT("Unsupported garment recoloring rejected"), Actor->ApplyAppearance(Appearance));
        World->DestroyActor(Actor);
    }
    return true;
}
#endif
