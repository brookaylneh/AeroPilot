#include "ScenarioHuman.h"
#include "Animation/AnimSequence.h"
#include "Animation/Skeleton.h"
#if WITH_EDITOR
#include "Animation/AnimData/IAnimationDataModel.h"
#include "Animation/AnimData/IAnimationDataController.h"
#endif

// Bake from an immutable source clip. Re-running authoring produces the same
// animation instead of applying another transform to an already edited pose.
bool AScenarioHuman::PrepareSituationAnimation(UAnimSequence *Animation, UAnimSequence *Reference,
                                               FName Situation)
{
#if WITH_EDITOR
    if (!Animation || !Animation->GetSkeleton() || !Reference || !Reference->GetDataModel() ||
        (Situation != TEXT("lost") && Situation != TEXT("injured")))
        return false;
    const bool Resting = Situation == TEXT("injured");
    const auto &Skeleton = Animation->GetSkeleton()->GetReferenceSkeleton();
    const auto *Model = Reference->GetDataModel();
    TArray<FName> Tracks;
    Model->GetBoneTrackNames(Tracks);
    TArray<FTransform> Pose = Skeleton.GetRefBonePose(), World;
    World.SetNum(Pose.Num());
    auto Index = [&](const TCHAR *Name) { return Skeleton.FindBoneIndex(FName(Name)); };
    for (const TCHAR *Name :
         {TEXT("pelvis"), TEXT("thigh_l"), TEXT("thigh_r"), TEXT("calf_l"), TEXT("calf_r"), TEXT("foot_l"),
          TEXT("foot_r"), TEXT("upperarm_l"), TEXT("upperarm_r"), TEXT("lowerarm_l"), TEXT("lowerarm_r"),
          TEXT("hand_l"), TEXT("hand_r"), TEXT("spine_03"), TEXT("head")})
        if (Index(Name) == INDEX_NONE)
            return false;
    auto Sample = [&](int32 Frame) {
        for (int32 I = 0; I < Pose.Num(); ++I)
            if (Tracks.Contains(Skeleton.GetBoneName(I)))
                Pose[I] = Model->GetBoneTrackTransform(Skeleton.GetBoneName(I), FFrameNumber(Frame));
    };
    auto Evaluate = [&]() {
        for (int32 I = 0; I < Pose.Num(); ++I)
        {
            const int32 Parent = Skeleton.GetParentIndex(I);
            World[I] = Parent == INDEX_NONE ? Pose[I] : Pose[I] * World[Parent];
        }
    };
    Sample(30);
    Evaluate();
    if (Resting)
    {
        const double Scale = World[Index(TEXT("pelvis"))].GetLocation().Z / 93.5;
        Pose[Index(TEXT("pelvis"))].SetTranslation(FVector(0, 0, 22 * Scale));
        auto Aim = [&](const TCHAR *Bone, const TCHAR *Child, FVector Target) {
            Evaluate();
            const int32 I = Index(Bone), Parent = Skeleton.GetParentIndex(I);
            const FVector Origin = World[I].GetLocation();
            const FQuat Delta =
                FQuat::FindBetweenNormals((World[Index(Child)].GetLocation() - Origin).GetSafeNormal(),
                                          (Target * Scale - Origin).GetSafeNormal());
            Pose[I].SetRotation(
                (World[Parent].GetRotation().Inverse() * Delta * World[I].GetRotation()).GetNormalized());
        };
        Aim(TEXT("thigh_l"), TEXT("calf_l"), FVector(15, 40, 13));
        Aim(TEXT("thigh_r"), TEXT("calf_r"), FVector(-15, 40, 13));
        Aim(TEXT("calf_l"), TEXT("foot_l"), FVector(18, 82, 9));
        Aim(TEXT("calf_r"), TEXT("foot_r"), FVector(-18, 82, 9));
        Aim(TEXT("upperarm_l"), TEXT("lowerarm_l"), FVector(30, 12, 46));
        Aim(TEXT("upperarm_r"), TEXT("lowerarm_r"), FVector(-30, 12, 46));
        Aim(TEXT("lowerarm_l"), TEXT("hand_l"), FVector(18, 35, 28));
        Aim(TEXT("lowerarm_r"), TEXT("hand_r"), FVector(-18, 35, 28));
    }
    const TArray<FTransform> RestPose = Pose;
    const int32 Frames = Resting ? 120 : Model->GetNumberOfFrames();
    struct FKeys
    {
        TArray<FVector> Positions, Scales;
        TArray<FQuat> Rotations;
    };
    TArray<FKeys> Keys;
    Keys.SetNum(Tracks.Num());
    for (int32 Frame = 0; Frame <= Frames; ++Frame)
    {
        const double Cycle = FMath::Sin(Frame * 2 * PI / Frames);
        if (Resting)
        {
            Pose = RestPose;
            auto &Spine = Pose[Index(TEXT("spine_03"))];
            Spine.SetRotation(Spine.GetRotation() *
                              FQuat(FVector::RightVector, FMath::DegreesToRadians(.35 * Cycle)));
        }
        else
        {
            Sample(Frame);
            Evaluate();
            const int32 Head = Index(TEXT("head")), Parent = Skeleton.GetParentIndex(Head);
            const FQuat Turn(FVector::UpVector, FMath::DegreesToRadians(18 * Cycle));
            Pose[Head].SetRotation(
                (World[Parent].GetRotation().Inverse() * Turn * World[Head].GetRotation()).GetNormalized());
        }
        for (int32 T = 0; T < Tracks.Num(); ++T)
        {
            const int32 I = Skeleton.FindBoneIndex(Tracks[T]);
            if (I == INDEX_NONE)
                continue;
            Keys[T].Positions.Add(Pose[I].GetTranslation());
            Keys[T].Rotations.Add(Pose[I].GetRotation());
            Keys[T].Scales.Add(Pose[I].GetScale3D());
        }
    }
    auto &Controller = Animation->GetController();
    Controller.OpenBracket(FText::FromString(TEXT("Author scenario situation")), false);
    Controller.RemoveAllBoneTracks(false);
    for (int32 T = 0; T < Tracks.Num(); ++T)
        if (!Keys[T].Positions.IsEmpty())
            Controller.AddBoneCurve(Tracks[T], false);
    Controller.SetFrameRate(Model->GetFrameRate(), false);
    Controller.SetNumberOfFrames(FFrameNumber(Frames), false);
    for (int32 T = 0; T < Tracks.Num(); ++T)
        if (!Keys[T].Positions.IsEmpty() &&
            !Controller.SetBoneTrackKeys(Tracks[T], Keys[T].Positions, Keys[T].Rotations, Keys[T].Scales,
                                         false))
        {
            Controller.CloseBracket(false);
            return false;
        }
    Controller.CloseBracket(false);
    return true;
#else
    return false;
#endif
}
