#include "ScenarioGrass.h"
#include "Components/BoxComponent.h"
#include "PCGComponent.h"
#include "PCGGraph.h"
#include "PCGSettings.h"
#include "PCGWorldActor.h"
#include "Subsystems/PCGSubsystem.h"
#include "UObject/UnrealType.h"
#include "Engine/World.h"
#include "RenderingThread.h"
#include "SceneInterface.h"

AScenarioGrass::AScenarioGrass()
{
    PrimaryActorTick.bCanEverTick = true;
    PrimaryActorTick.bStartWithTickEnabled = false;
    PrimaryActorTick.TickGroup = TG_PostUpdateWork;
    Bounds = CreateDefaultSubobject<UBoxComponent>(TEXT("Bounds"));
    SetRootComponent(Bounds);
    Bounds->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Grass = CreateDefaultSubobject<UPCGComponent>(TEXT("Grass"));
    Grass->GenerationTrigger = EPCGComponentGenerationTrigger::GenerateAtRuntime;
}

void AScenarioGrass::Configure()
{
    checkf(StudioGraph && MissionGraph, TEXT("Grass requires both authored profiles"));
    UPCGSubsystem *Subsystem = GetWorld()->GetSubsystem<UPCGSubsystem>();
    check(Subsystem);
    APCGWorldActor *WorldActor = Subsystem->GetPCGWorldActor();
    checkf(WorldActor, TEXT("Grass requires a saved PCGWorldActor, including in non-WP levels"));
    WorldActor->bTreatPlayerControllersAsGenerationSources = false;
#if WITH_EDITOR
    for (UPCGGraph *Graph : {StudioGraph.Get(), MissionGraph.Get()})
    {
        Graph->SetCommentNodes({});
        Graph->SetExtraEditorNodes({});
        Graph->MarkPackageDirty();
    }
#endif
    Grass->SetGraphLocal(StudioGraph);
    Grass->SetIsPartitioned(true);
    check(Grass->IsPartitioned());
}

void AScenarioGrass::SetKernelSource(UPCGSettings *Settings, const FString &Source)
{
#if WITH_EDITOR
    // UE 5.8's shader text properties have no Python editor exposure. Mirror the native
    // code editor's property notifications without depending on PCG's private headers.
    check(Settings && Settings->GetClass()->GetFName() == TEXT("PCGCustomHLSLSettings"));
    for (const TCHAR *Name : {TEXT("ShaderFunctions"), TEXT("ShaderSource")})
    {
        FStrProperty *Property = FindFProperty<FStrProperty>(Settings->GetClass(), Name);
        check(Property);
        Settings->PreEditChange(Property);
        Settings->Modify();
        Property->SetPropertyValue_InContainer(Settings, FCString::Strcmp(Name, TEXT("ShaderSource")) == 0 ? Source : FString());
        FPropertyChangedEvent Event(Property, EPropertyChangeType::ValueSet);
        static_cast<UObject *>(Settings)->PostEditChangeProperty(Event);
    }
    Settings->OnSettingsChangedDelegate.Broadcast(Settings, EPCGChangeType::ShaderSource | EPCGChangeType::Structural);
#else
    checkNoEntry();
#endif
}

void AScenarioGrass::BeginPlay()
{
    Super::BeginPlay();
    checkf(Grass->IsPartitioned() && Grass->IsManagedByRuntimeGenSystem() &&
               Grass->GetGraph() == StudioGraph && MissionGraph,
           TEXT("Invalid authored runtime grass"));
}

void AScenarioGrass::EnterMission()
{
    check(Grass->GetGraph() == StudioGraph);
    Grass->SetGraphLocal(MissionGraph);
    SetActorTickEnabled(true);
}

void AScenarioGrass::EnterStudio()
{
    check(Grass->GetGraph() == MissionGraph);
    Grass->SetGraphLocal(StudioGraph);
    SetActorTickEnabled(false);
}

void AScenarioGrass::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    // Mission disables viewport rendering. Pending component changes must reach the
    // renderer before PCG can upload instances; scene updates then dispatch its compute.
    GetWorld()->SendAllEndOfFrameUpdates();
    FSceneInterface *Scene = GetWorld()->Scene;
    check(Scene);
    UE::RenderCommandPipe::FSyncScope SyncScope;
    ENQUEUE_RENDER_COMMAND(MissionGrassSceneUpdate)([Scene](FRHICommandListImmediate &RHICmdList)
    {
        Scene->UpdateAllPrimitiveSceneInfos(RHICmdList);
    });
}
