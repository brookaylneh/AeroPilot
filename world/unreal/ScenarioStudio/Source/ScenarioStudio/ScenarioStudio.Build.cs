using UnrealBuildTool;
public class ScenarioStudio : ModuleRules {
    public ScenarioStudio(ReadOnlyTargetRules Target) : base(Target) {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        bEnableExceptions = true;
        PrivateDependencyModuleNames.AddRange(new[] { "AirSim", "UMG", "ImageWrapper", "TraceLog" });
        PublicDependencyModuleNames.AddRange(new[] { "Core", "CoreUObject", "Engine", "InputCore", "Json", "JsonUtilities" });
        PrivateDependencyModuleNames.AddRange(new[] { "PixelStreaming2", "PixelStreaming2Core", "PixelStreaming2Input", "PixelCapture", "RHI", "RenderCore", "Slate", "SlateCore" });
        if (Target.bBuildEditor) PrivateDependencyModuleNames.AddRange(new[] { "UnrealEd", "Landscape" });
    }
}
