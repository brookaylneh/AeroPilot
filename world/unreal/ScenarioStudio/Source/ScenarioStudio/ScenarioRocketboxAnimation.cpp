#include "ScenarioHuman.h"
#include "Animation/AnimSequence.h"
#include "Animation/Skeleton.h"
#include "Engine/SkeletalMesh.h"
#include "Components/SkeletalMeshComponent.h"
#if WITH_EDITOR
#include "Animation/AnimData/IAnimationDataModel.h"
#include "Animation/AnimData/IAnimationDataController.h"
#endif


bool AScenarioHuman::BindRocketboxSkeleton(USkeletalMesh *Mesh, USkeleton *Skeleton)
{
#if WITH_EDITOR
    if (!Mesh || !Skeleton || !Mesh->GetSkeleton() ||
        Mesh->GetRefSkeleton().GetRawRefBoneInfo() != Skeleton->GetReferenceSkeleton().GetRawRefBoneInfo())
        return false;
    Mesh->SetSkeleton(Skeleton);
    return Mesh->GetSkeleton() == Skeleton;
#else
    return false;
#endif
}

bool AScenarioHuman::PrepareRocketboxAnimation(UAnimSequence *Animation, UAnimSequence *Reference,
                                               FName Activity)
{
#if WITH_EDITOR
    if (!Animation || !Reference || Animation == Reference || !Animation->GetSkeleton() ||
        !Reference->GetDataModel() || Animation->GetSkeleton() != Reference->GetSkeleton() ||
        !(Activity == TEXT("lost") || Activity == TEXT("waiting") || Activity == TEXT("walking") ||
          Activity == TEXT("injured")))
        return false;
    const auto &Skeleton = Animation->GetSkeleton()->GetReferenceSkeleton();
    const auto *Model = Reference->GetDataModel();
    TArray<FName> Tracks;
    Model->GetBoneTrackNames(Tracks);
    const TArray<FTransform> ReferencePose = Skeleton.GetRefBonePose();
    const int32 Root = Skeleton.FindBoneIndex(TEXT("Bip01"));
    auto Index = [&](const TCHAR *Name) { return Skeleton.FindBoneIndex(FName(Name)); };
    for (const TCHAR *Name :
         {TEXT("Bip01"), TEXT("Bip01-Pelvis"), TEXT("Bip01-Spine2"), TEXT("Bip01-Head"),
          TEXT("Bip01-L-Thigh"), TEXT("Bip01-R-Thigh"), TEXT("Bip01-L-Calf"), TEXT("Bip01-R-Calf"),
          TEXT("Bip01-L-Foot"), TEXT("Bip01-R-Foot"), TEXT("Bip01-L-UpperArm"), TEXT("Bip01-R-UpperArm"),
          TEXT("Bip01-L-Forearm"), TEXT("Bip01-R-Forearm"), TEXT("Bip01-L-Hand"), TEXT("Bip01-R-Hand")})
        if (Index(Name) == INDEX_NONE || !Tracks.Contains(FName(Name)))
            return false;
    const FVector SourceRoot = Model->GetBoneTrackTransform(TEXT("Bip01"), FFrameNumber(0)).GetLocation();
    const FVector TargetRoot = ReferencePose[Root].GetLocation();
    if (Root != 0 || SourceRoot.Z < 50 || TargetRoot.Z < 50 || Model->GetNumberOfFrames() < 2)
        return false;
    const double Proportion = TargetRoot.Z / SourceRoot.Z;
    if (Proportion < .5 || Proportion > 1.5)
        return false;
    const bool Resting = Activity == TEXT("injured");
    const int32 Frames = Resting ? 120 : Model->GetNumberOfFrames();
    TArray<FTransform> Pose = ReferencePose, World;
    World.SetNum(Pose.Num());
    struct FKeys
    {
        TArray<FVector> Positions, Scales;
        TArray<FQuat> Rotations;
    };
    TArray<FKeys> Keys;
    Keys.SetNum(Tracks.Num());
    auto Evaluate = [&]() {
        for (int32 I = 0; I < Pose.Num(); ++I)
        {
            const int32 Parent = Skeleton.GetParentIndex(I);
            World[I] = Parent == INDEX_NONE ? Pose[I] : Pose[I] * World[Parent];
        }
    };
    for (int32 Frame = 0; Frame <= Frames; ++Frame)
    {
        for (int32 I = 0; I < Tracks.Num(); ++I)
        {
            const int32 Bone = Skeleton.FindBoneIndex(Tracks[I]);
            if (Bone == INDEX_NONE)
                return false;
            Pose[Bone] = Model->GetBoneTrackTransform(Tracks[I], FFrameNumber(Resting ? 0 : Frame));
            // Keep each child's limb lengths; the source clips use adult proportions.
            Pose[Bone].SetTranslation(ReferencePose[Bone].GetTranslation());
            Pose[Bone].SetScale3D(ReferencePose[Bone].GetScale3D());
        }
        const auto Source = Model->GetBoneTrackTransform(TEXT("Bip01"), FFrameNumber(Resting ? 0 : Frame));
        FVector Position = TargetRoot;
        Position.Z += (Source.GetLocation().Z - SourceRoot.Z) * Proportion;
        // Scenario behavior owns horizontal motion and heading.
        Pose[Root].SetTranslation(Position);
        if (Resting)
        {
            const double Scale = TargetRoot.Z / 100.;
            Position.Z = 22 * Scale;
            Pose[Root].SetTranslation(Position);
            auto Aim = [&](const TCHAR *Bone, const TCHAR *Child, FVector Target) {
                Evaluate();
                const int32 I = Index(Bone), Parent = Skeleton.GetParentIndex(I);
                const FVector Origin = World[I].GetLocation();
                const FQuat Delta = FQuat::FindBetweenNormals(
                    (World[Index(Child)].GetLocation() - Origin).GetSafeNormal(),
                    (Target * Scale - Origin).GetSafeNormal());
                Pose[I].SetRotation(
                    (World[Parent].GetRotation().Inverse() * Delta * World[I].GetRotation()).GetNormalized());
            };
            Aim(TEXT("Bip01-L-Thigh"), TEXT("Bip01-L-Calf"), FVector(15, 40, 13));
            Aim(TEXT("Bip01-R-Thigh"), TEXT("Bip01-R-Calf"), FVector(-15, 40, 13));
            Aim(TEXT("Bip01-L-Calf"), TEXT("Bip01-L-Foot"), FVector(18, 82, 9));
            Aim(TEXT("Bip01-R-Calf"), TEXT("Bip01-R-Foot"), FVector(-18, 82, 9));
            Aim(TEXT("Bip01-L-UpperArm"), TEXT("Bip01-L-Forearm"), FVector(30, 12, 46));
            Aim(TEXT("Bip01-R-UpperArm"), TEXT("Bip01-R-Forearm"), FVector(-30, 12, 46));
            Aim(TEXT("Bip01-L-Forearm"), TEXT("Bip01-L-Hand"), FVector(18, 35, 28));
            Aim(TEXT("Bip01-R-Forearm"), TEXT("Bip01-R-Hand"), FVector(-18, 35, 28));
            auto &Spine = Pose[Index(TEXT("Bip01-Spine2"))];
            Spine.SetRotation(Spine.GetRotation() * FQuat(FVector::RightVector,
                FMath::DegreesToRadians(.35 * FMath::Sin(Frame * 2 * PI / Frames))));
        }
        for (int32 I = 0; I < Tracks.Num(); ++I)
        {
            const auto &Transform = Pose[Skeleton.FindBoneIndex(Tracks[I])];
            Keys[I].Positions.Add(Transform.GetLocation());
            Keys[I].Rotations.Add(Transform.GetRotation());
            Keys[I].Scales.Add(Transform.GetScale3D());
        }
    }
    auto &Controller = Animation->GetController();
    Controller.OpenBracket(FText::FromString(TEXT("Retarget Rocketbox scenario motion")), false);
    Controller.RemoveAllBoneTracks(false);
    Controller.SetFrameRate(Resting ? FFrameRate(30, 1) : Model->GetFrameRate(), false);
    Controller.SetNumberOfFrames(FFrameNumber(Frames), false);
    for (int32 I = 0; I < Tracks.Num(); ++I)
    {
        Controller.AddBoneCurve(Tracks[I], false);
        if (!Controller.SetBoneTrackKeys(Tracks[I], Keys[I].Positions, Keys[I].Rotations, Keys[I].Scales, false))
        {
            Controller.CloseBracket(false);
            return false;
        }
    }
    Controller.CloseBracket(false);
    return true;
#else
    return false;
#endif
}

bool AScenarioHuman::PreviewRocketboxPose(USkeletalMeshComponent *Mesh, UAnimSequence *Animation, float Time)
{
#if WITH_EDITOR
    if (!Mesh || !Mesh->GetSkeletalMeshAsset() || !Animation || Time < 0 || Time > Animation->GetPlayLength() ||
        Mesh->GetSkeletalMeshAsset()->GetSkeleton() != Animation->GetSkeleton())
        return false;
    Mesh->SetAnimationMode(EAnimationMode::AnimationSingleNode);
    Mesh->PlayAnimation(Animation, false);
    Mesh->SetPosition(Time, false);
    Mesh->TickAnimation(0, false);
    Mesh->RefreshBoneTransforms();
    Mesh->bPauseAnims = true;
    return true;
#else
    return false;
#endif
}
