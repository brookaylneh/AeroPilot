#include "Modules/ModuleManager.h"
#include "Trace/Trace.h"

class FScenarioStudioModule : public FDefaultGameModuleImpl
{
public:
    void StartupModule() override
    {
#if !WITH_EDITOR
        // UE 5.8 has no runtime switch for TCP trace control. Panic permanently
        // mutes TraceLog and prevents its first update from opening port 1985.
        // CSV frame/GPU profiling is independent of TraceLog.
        UE::Trace::Panic();
#endif
    }
};

IMPLEMENT_PRIMARY_GAME_MODULE(FScenarioStudioModule, ScenarioStudio, "ScenarioStudio");
