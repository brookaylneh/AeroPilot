#include "ScenarioHuman.h"
#include "Components/ChildActorComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Animation/AnimSequence.h"
#include "Animation/Skeleton.h"
#include "Animation/AnimSingleNodeInstance.h"

bool AScenarioHuman::PrepareStandingAnimation(UAnimSequence *Animation, USkeleton *TargetSkeleton)
{
#if WITH_EDITOR
    if (!Animation || !Animation->GetSkeleton() || !TargetSkeleton)
        return false;
    const FReferenceSkeleton &Source = Animation->GetSkeleton()->GetReferenceSkeleton();
    const FReferenceSkeleton &Target = TargetSkeleton->GetReferenceSkeleton();
    for (const FName Bone :
         {FName("root"), FName("pelvis"), FName("spine_01"), FName("foot_l"), FName("foot_r")})
    {
        if (Source.FindBoneIndex(Bone) == INDEX_NONE || Target.FindBoneIndex(Bone) == INDEX_NONE)
            return false;
    }
    return Animation->ReplaceSkeleton(TargetSkeleton, false);
#else
    return false;
#endif
}

AScenarioHuman::AScenarioHuman()
{
    Character = CreateDefaultSubobject<UChildActorComponent>(TEXT("Character"));
    Character->SetupAttachment(RootComponent);
    Backpack = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Backpack"));
    Backpack->SetupAttachment(RootComponent);
    Backpack->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Backpack->SetVisibility(false);
}
bool AScenarioHuman::ApplyAppearance_Implementation(const FScenarioAppearance &Selection)
{
    if (!CharacterClass)
        return false;
    // SetChildActorClass recreates the child even when the class is unchanged.
    // Construct once; clothing updates must preserve the body and its animation.
    if (!Body)
        Character->SetChildActorClass(CharacterClass);
    AActor *Actor = Character->GetChildActor();
    if (!Actor)
        return false;
    TArray<AActor *> Actors;
    Actor->GetAllChildActors(Actors, true);
    Actors.AddUnique(Actor);
    TArray<UMeshComponent *> Meshes;
    for (AActor *Entry : Actors)
    {
        TArray<UMeshComponent *> Components;
        Entry->GetComponents(Components);
        Meshes.Append(Components);
    }
    TSet<FName> Applied;
    for (const auto &Binding : TintBindings)
    {
        const FName *Option = Selection.Options.Find(Binding.AppearanceKey);
        const FLinearColor *Color = Option ? ColorPalette.Find(*Option) : nullptr;
        UMeshComponent **Found = Meshes.FindByPredicate(
            [&](UMeshComponent *Mesh) { return Mesh->GetFName() == Binding.ComponentName; });
        if (!Color || !Found || Binding.MaterialSlot < 0 ||
            Binding.MaterialSlot >= (*Found)->GetNumMaterials())
            return false;
        UMaterialInterface *Material = (*Found)->GetMaterial(Binding.MaterialSlot);
        FLinearColor Original;
        if (!Material ||
            !Material->GetVectorParameterValue(FMaterialParameterInfo(Binding.ParameterName), Original))
            return false;
        UMaterialInstanceDynamic *Dynamic = (*Found)->CreateDynamicMaterialInstance(Binding.MaterialSlot);
        if (!Dynamic)
            return false;
        Dynamic->SetVectorParameterValue(Binding.ParameterName, *Color);
        Applied.Add(Binding.AppearanceKey);
    }
    for (const auto &Pair : Selection.Options)
    {
        if (Pair.Key != TEXT("backpackColor") && Pair.Key.ToString().EndsWith(TEXT("Color")) &&
            !Applied.Contains(Pair.Key) &&
            !(TintBindings.IsEmpty() && Pair.Value == TEXT("original") &&
              (Pair.Key == TEXT("topColor") || Pair.Key == TEXT("bottomColor"))))
            return false;
    }
    if (const FName *Pack = Selection.Options.Find(TEXT("backpackId")))
    {
        const bool Show = *Pack != TEXT("none");
        if (Show && !Backpack->GetStaticMesh())
            return false;
        Backpack->SetVisibility(Show);
        if (Show)
        {
            const FName *Option = Selection.Options.Find(TEXT("backpackColor"));
            const FLinearColor *Color = Option ? ColorPalette.Find(*Option) : nullptr;
            if (!Color)
                return false;
            if (auto *Material = Backpack->CreateDynamicMaterialInstance(0))
                Material->SetVectorParameterValue(TEXT("Color"), *Color);
        }
    }
    if (!Body)
    {
        for (auto *Mesh : Meshes)
            if (Mesh->GetFName() == BodyComponentName)
                Body = Cast<USkeletalMeshComponent>(Mesh);
        if (!Body || !StandingPose ||
            (Backpack->GetStaticMesh() && !Body->DoesSocketExist(TEXT("spine_03"))))
            return false;
        // The assembled body can be hidden beneath its visible clothing. It
        // must still tick, because the clothing and face follow its bone pose.
        Body->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
        Body->bPauseAnims = false;
        Body->SetComponentTickEnabled(true);
        Body->SetAnimationMode(EAnimationMode::AnimationSingleNode);
        Body->PlayAnimation(StandingPose, true);
        Body->TickAnimation(0, false);
        Body->RefreshBoneTransforms();
        if (Backpack->GetStaticMesh())
            Backpack->AttachToComponent(Body, FAttachmentTransformRules::KeepWorldTransform, TEXT("spine_03"));
        PlayingAnimation = StandingPose;
    }
    return true;
}

bool AScenarioHuman::ApplySituation(FName Situation)
{
    if (!SituationAnimations.Contains(Situation))
        return false;
    CurrentSituation = Situation;
    return ApplyMotion(false);
}

bool AScenarioHuman::ApplyMotion(bool Walking, double Time)
{
    const auto *Animation = SituationAnimations.Find(Walking ? FName(TEXT("walking")) : CurrentSituation);
    if (!Body || !Animation || !*Animation)
        return false;
    if (PlayingAnimation == *Animation && Time < 0)
        return true;
    if (PlayingAnimation != *Animation)
    {
        Body->PlayAnimation(*Animation, true);
        PlayingAnimation = *Animation;
    }
    Body->SetPlayRate(Walking ? .65f : 1.f);
    if (Time >= 0)
        Body->SetPosition(FMath::Fmod(Time, (*Animation)->GetPlayLength()), false);
    auto *Instance = Body->GetSingleNodeInstance();
    if (!Instance || Instance->GetCurrentAsset() != *Animation)
        return false;
    Body->TickAnimation(0, false);
    Body->RefreshBoneTransforms();
    return true;
}

double AScenarioHuman::AnimationTime() const
{
    check(Body && Body->GetSingleNodeInstance());
    return Body->GetSingleNodeInstance()->GetCurrentTime();
}
