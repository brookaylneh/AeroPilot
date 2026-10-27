#include "ScenarioHuman.h"
#include "Animation/AnimSequence.h"
#include "Animation/Skeleton.h"
#if WITH_EDITOR
#include "Animation/AnimData/IAnimationDataModel.h"
#include "Animation/AnimData/IAnimationDataController.h"
#endif

// Author finite loops onto the existing skeletons; no runtime bone manipulation.
// Sources stay immutable so rerunning asset preparation cannot compound transforms.
bool AScenarioHuman::PrepareActivityAnimation(UAnimSequence *Animation, UAnimSequence *Reference,
                                              FName Activity)
{
#if WITH_EDITOR
    if (!Animation || !Reference || !Reference->GetDataModel() || !Animation->GetSkeleton())
        return false;
    const bool Signaling = Activity == TEXT("signaling");
    const bool Walk = Activity == TEXT("walking"), Wave = Activity == TEXT("waiting"),
               Rest = Activity == TEXT("injured"), StockWalk = Activity == TEXT("stock-walk");
    if (!Walk && !Wave && !Rest && !StockWalk && !Signaling)
        return false;
    const auto &Skeleton = Animation->GetSkeleton()->GetReferenceSkeleton();
    const auto *Model = Reference->GetDataModel();
    TArray<FName> Tracks;
    Model->GetBoneTrackNames(Tracks);
    TArray<FTransform> Pose = Skeleton.GetRefBonePose(), World;
    World.SetNum(Pose.Num());
    auto Index = [&](const TCHAR *Name) { return Skeleton.FindBoneIndex(FName(Name)); };
    for (const TCHAR *Name :
         {TEXT("root"), TEXT("pelvis"), TEXT("thigh_l"), TEXT("thigh_r"), TEXT("calf_l"), TEXT("calf_r"),
          TEXT("foot_l"), TEXT("foot_r"), TEXT("upperarm_l"), TEXT("upperarm_r"), TEXT("lowerarm_l"),
          TEXT("lowerarm_r"), TEXT("hand_l"), TEXT("hand_r"), TEXT("spine_03"), TEXT("head")})
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
    auto Aim = [&](int32 I, int32 Child, const FVector &Target) {
        Evaluate();
        const int32 Parent = Skeleton.GetParentIndex(I);
        const FVector Origin = World[I].GetLocation();
        const FQuat Delta = FQuat::FindBetweenNormals((World[Child].GetLocation() - Origin).GetSafeNormal(),
                                                      (Target - Origin).GetSafeNormal());
        Pose[I].SetRotation(
            (World[Parent].GetRotation().Inverse() * Delta * World[I].GetRotation()).GetNormalized());
    };
    auto Turn = [&](int32 I, FVector Axis, double Degrees) {
        Evaluate();
        const int32 Parent = Skeleton.GetParentIndex(I);
        const FQuat Delta(Axis, FMath::DegreesToRadians(Degrees));
        Pose[I].SetRotation(
            (World[Parent].GetRotation().Inverse() * Delta * World[I].GetRotation()).GetNormalized());
    };
    Sample(0);
    Evaluate();
    const TArray<FTransform> NeutralWorld = World;
    const double Scale = World[Index(TEXT("head"))].GetLocation().Z / (Rest ? 80. : 155.);
    const int32 Frames = StockWalk ? Model->GetNumberOfFrames() : Walk ? 42 : Wave ? 420 : 540;
    struct FKeys
    {
        TArray<FVector> Positions, Scales;
        TArray<FQuat> Rotations;
    };
    TArray<FKeys> Keys;
    Keys.SetNum(Tracks.Num());
    for (int32 Frame = 0; Frame <= Frames; ++Frame)
    {
        Sample(StockWalk ? Frame : Frame % Model->GetNumberOfFrames());
        const double Phase = double(Frame) / Frames, T = Phase * 2 * PI;
        if (StockWalk)
        {
            // Actor movement owns displacement; the stock cycle supplies gait.
            FVector Root = Pose[Index(TEXT("root"))].GetTranslation();
            Root.X = NeutralWorld[Index(TEXT("root"))].GetLocation().X;
            Root.Y = NeutralWorld[Index(TEXT("root"))].GetLocation().Y;
            Pose[Index(TEXT("root"))].SetTranslation(Root);
        }
        else if (Wave)
        {
            // Raise, signal, lower, then look around before the next wave.
            const double Envelope =
                Phase > .12 && Phase < .65
                    ? FMath::Clamp(FMath::Min((Phase - .12) / .12, (.65 - Phase) / .12), 0., 1.)
                    : 0.;
            const double Blend = Envelope * Envelope * (3 - 2 * Envelope);
            const TArray<FTransform> Idle = Pose;
            Aim(Index(TEXT("upperarm_r")), Index(TEXT("lowerarm_r")), FVector(-34, 6, 137) * Scale);
            Aim(Index(TEXT("lowerarm_r")), Index(TEXT("hand_r")),
                FVector(-36 + 12 * FMath::Sin(T * 7), 8, 177) * Scale);
            for (const TCHAR *Name : {TEXT("upperarm_r"), TEXT("lowerarm_r")})
            {
                const int32 I = Index(Name);
                Pose[I].SetRotation(FQuat::Slerp(Idle[I].GetRotation(), Pose[I].GetRotation(), Blend));
            }
            Turn(Index(TEXT("head")), FVector::UpVector, 20 * FMath::Sin(T));
        }
        else if (Signaling)
        {
            // The scans join clothing/accessories to the arms. Keep those
            // contact points intact while looking in different directions.
            Turn(Index(TEXT("spine_03")), FVector::UpVector, 3 * FMath::Sin(T));
            Turn(Index(TEXT("head")), FVector::UpVector, 25 * FMath::Sin(T));
            Turn(Index(TEXT("head")), FVector::ForwardVector, 4 * FMath::Sin(T * 2));
        }
        else if (Rest)
        {
            Turn(Index(TEXT("spine_03")), FVector::ForwardVector, 3 * FMath::Sin(T));
            Turn(Index(TEXT("head")), FVector::ForwardVector, 8 * (1 - FMath::Cos(T)));
            Turn(Index(TEXT("head")), FVector::UpVector, 9 * FMath::Sin(T * 2));
            Turn(Index(TEXT("lowerarm_l")), FVector::ForwardVector, 4 * FMath::Sin(T * 2));
        }
        else
        {
            // The child scans have their own skeletons. Solve a short in-place
            // gait from their measured leg lengths instead of scaling adult bones.
            for (int32 Side = 0; Side < 2; ++Side)
            {
                const int32 Hip = Index(Side ? TEXT("thigh_r") : TEXT("thigh_l"));
                const int32 Knee = Index(Side ? TEXT("calf_r") : TEXT("calf_l"));
                const int32 Foot = Index(Side ? TEXT("foot_r") : TEXT("foot_l"));
                const double Cycle = FMath::Fmod(Phase + .5 * Side, 1.);
                FVector Target = NeutralWorld[Foot].GetLocation();
                Target.Y += 22 * Scale * FMath::Cos(Cycle * 2 * PI);
                Target.Z += 7 * Scale * FMath::Max(0., -FMath::Sin(Cycle * 2 * PI));
                Evaluate();
                const FVector Origin = World[Hip].GetLocation();
                const double Upper =
                    FVector::Dist(NeutralWorld[Hip].GetLocation(), NeutralWorld[Knee].GetLocation());
                const double Lower =
                    FVector::Dist(NeutralWorld[Knee].GetLocation(), NeutralWorld[Foot].GetLocation());
                FVector Axis = (Target - Origin).GetSafeNormal();
                const double Distance = FMath::Clamp(FVector::Dist(Origin, Target), 1., Upper + Lower - .1);
                Target = Origin + Axis * Distance;
                const double Along = (Upper * Upper + Distance * Distance - Lower * Lower) / (2 * Distance);
                const FVector Bend =
                    (FVector::RightVector - Axis * FVector::DotProduct(Axis, FVector::RightVector))
                        .GetSafeNormal();
                const FVector KneeTarget =
                    Origin + Axis * Along + Bend * FMath::Sqrt(FMath::Max(0., Upper * Upper - Along * Along));
                Aim(Hip, Knee, KneeTarget);
                Aim(Knee, Foot, Target);
                Evaluate();
                Pose[Foot].SetRotation(
                    (World[Knee].GetRotation().Inverse() * NeutralWorld[Foot].GetRotation()).GetNormalized());
                Turn(Index(Side ? TEXT("upperarm_r") : TEXT("upperarm_l")), FVector::ForwardVector,
                     8 * FMath::Sin(T + Side * PI));
            }
        }
        for (int32 I = 0; I < Tracks.Num(); ++I)
        {
            const int32 Bone = Skeleton.FindBoneIndex(Tracks[I]);
            if (Bone == INDEX_NONE)
                return false;
            Keys[I].Positions.Add(Pose[Bone].GetTranslation());
            Keys[I].Rotations.Add(Pose[Bone].GetRotation());
            Keys[I].Scales.Add(Pose[Bone].GetScale3D());
        }
    }
    auto &Controller = Animation->GetController();
    Controller.OpenBracket(FText::FromString(TEXT("Author scenario activity")), false);
    Controller.RemoveAllBoneTracks(false);
    Controller.SetFrameRate(StockWalk ? Model->GetFrameRate() : FFrameRate(30, 1), false);
    Controller.SetNumberOfFrames(FFrameNumber(Frames), false);
    for (int32 I = 0; I < Tracks.Num(); ++I)
    {
        Controller.AddBoneCurve(Tracks[I], false);
        if (!Controller.SetBoneTrackKeys(Tracks[I], Keys[I].Positions, Keys[I].Rotations, Keys[I].Scales,
                                         false))
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
