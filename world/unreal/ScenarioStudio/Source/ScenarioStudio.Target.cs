using UnrealBuildTool;
public class ScenarioStudioTarget : TargetRules {
    public ScenarioStudioTarget(TargetInfo Target) : base(Target) {
        Type = TargetType.Game;
        DefaultBuildSettings = BuildSettingsVersion.Latest;
        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
        ExtraModuleNames.Add("ScenarioStudio");
    }
}
