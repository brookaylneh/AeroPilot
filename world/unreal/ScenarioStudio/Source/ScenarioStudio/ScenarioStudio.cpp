#include "Modules/ModuleManager.h"
#include "HAL/IConsoleManager.h"
#include "Trace/Trace.h"

class FScenarioStudioModule : public FDefaultGameModuleImpl
{
public:
    void StartupModule() override
    {
        // PCG's GPU landscape-mask exporter requires these shaders/resources in cooked worlds.
        IConsoleVariable *GrassMaps = IConsoleManager::Get().FindConsoleVariable(
            TEXT("grass.GrassMap.AlwaysBuildRuntimeGenerationResources"));
        check(GrassMaps);
        GrassMaps->Set(1, ECVF_SetByCode);
#if !WITH_EDITOR
        // UE 5.8 has no runtime switch for TCP trace control. Panic permanently
        // mutes TraceLog and prevents its first update from opening port 1985.
        // CSV frame/GPU profiling is independent of TraceLog.
        UE::Trace::Panic();
#endif
    }
};

IMPLEMENT_PRIMARY_GAME_MODULE(FScenarioStudioModule, ScenarioStudio, "ScenarioStudio");
