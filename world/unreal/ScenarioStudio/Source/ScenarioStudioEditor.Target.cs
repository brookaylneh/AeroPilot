using UnrealBuildTool;
public class ScenarioStudioEditorTarget : TargetRules {
    public ScenarioStudioEditorTarget(TargetInfo Target) : base(Target) {
        Type = TargetType.Editor;
        DefaultBuildSettings = BuildSettingsVersion.Latest;
        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
        ExtraModuleNames.Add("ScenarioStudio");
    }
}
