#include "ScenarioManager.h"
#include "Dom/JsonObject.h"

namespace
{
bool InsideWalkArea(const FVector &P, const FVector &Origin)
{
    return FMath::Abs(P.X - Origin.X) <= FScenarioBehavior::HalfWidthCm &&
           FMath::Abs(P.Y - Origin.Y) <= FScenarioBehavior::HalfWidthCm;
}
TSharedRef<FJsonObject> PointJson(const FVector &P)
{
    auto Json = MakeShared<FJsonObject>();
    Json->SetNumberField(TEXT("x"), P.X);
    Json->SetNumberField(TEXT("y"), P.Y);
    Json->SetNumberField(TEXT("z"), P.Z);
    return Json;
}
bool ReadPoint(const TSharedPtr<FJsonObject> &Json, const TCHAR *Key, FVector &P)
{
    const TSharedPtr<FJsonObject> *Point;
    return Json->TryGetObjectField(Key, Point) && (*Point)->TryGetNumberField(TEXT("x"), P.X) &&
           (*Point)->TryGetNumberField(TEXT("y"), P.Y) && (*Point)->TryGetNumberField(TEXT("z"), P.Z) &&
           !P.ContainsNaN();
}
}

bool AScenarioManager::SetDifficulty(const FString &Value, FString &Error)
{
    if (Value != TEXT("easy") && Value != TEXT("medium") && Value != TEXT("hard"))
    {
        Error = TEXT("Unknown report difficulty");
        return false;
    }
    Difficulty = Value;
    if (bPlaced)
        ResetBehavior();
    return true;
}

void AScenarioManager::ResetBehavior()
{
    check(Person);
    Behavior = FScenarioBehavior();
    Behavior.Origin = Behavior.Destination = Person->GetActorLocation();
    verify(Person->ApplyMotion(false, 0));
}

void AScenarioManager::SetBehaviorActive(bool Active)
{
    if (bBehaviorActive == Active)
    {
        if (!Active && Person)
            verify(Person->ApplyMotion(false));
        return;
    }
    if (Person && bPlaced)
    {
        if (!Active)
            Behavior.AnimationTime = Person->AnimationTime();
        verify(Person->ApplyMotion(Active && Behavior.bWalking, Active ? Behavior.AnimationTime : -1));
    }
    bBehaviorActive = Active;
}

void AScenarioManager::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (!bBehaviorActive || !bPlaced || SituationId != TEXT("lost"))
        return;
    const auto *Preset = Catalog->People.FindByPredicate([this](const FScenarioPersonPreset &P) {
        return P.Id == Appearance.Options.FindRef(TEXT("personId"));
    });
    check(Person && Preset);
    if (!Preset->bCanWalk)
        return;
    const double Dt = FMath::Min(static_cast<double>(DeltaSeconds), .1);
    const double HalfWidthCm = FScenarioBehavior::HalfWidthCm;
    Behavior.PauseSeconds = FMath::Max(0., Behavior.PauseSeconds - Dt);
    if (Behavior.PauseSeconds > 0)
        return;
    FRandomStream Random(Behavior.Seed);
    FString Error;
    if (!Behavior.bWalking)
    {
        const FVector Start = Person->GetActorLocation();
        // Rejected destinations are ordinary terrain constraints. The person
        // waits and looks around if no clear local path is found.
        for (int32 Attempt = 0; Attempt < 8 && !Behavior.bWalking; ++Attempt)
        {
            const FVector Point = Behavior.Origin + FVector(Random.FRandRange(-HalfWidthCm, HalfWidthCm),
                                                            Random.FRandRange(-HalfWidthCm, HalfWidthCm), 0);
            FVector Ground;
            if (FVector::Dist2D(Start, Point) < 120 ||
                !ValidateGround(RegionId, Point, *Preset, SituationId, Ground, Error))
                continue;
            bool Clear = true;
            const int32 Steps = FMath::CeilToInt(FVector::Dist2D(Start, Ground) / 25.);
            for (int32 I = 1; I <= Steps && Clear; ++I)
            {
                FVector Supported;
                Clear = ValidateGround(RegionId, FMath::Lerp(Start, Ground, double(I) / Steps), *Preset,
                                       SituationId, Supported, Error);
            }
            if (Clear)
            {
                Behavior.Destination = Ground;
                Behavior.bWalking = true;
            }
        }
        Behavior.Seed = Random.GetCurrentSeed();
        if (!Behavior.bWalking)
        {
            Behavior.PauseSeconds = 3;
            return;
        }
    }
    const FVector Start = Person->GetActorLocation();
    const FVector Direction = (Behavior.Destination - Start).GetSafeNormal2D();
    const double Heading = Direction.Rotation().Yaw;
    const FRotator Rotation =
        FMath::RInterpConstantTo(Person->GetActorRotation(), FRotator(0, Heading, 0), Dt, 100);
    Person->SetActorRotation(Rotation);
    const bool Facing = FMath::Abs(FMath::FindDeltaAngleDegrees(Rotation.Yaw, Heading)) < 12;
    verify(Person->ApplyMotion(true));
    if (!Facing)
        return;
    const double Speed = 37.5 * Preset->HeightCm / 170.;
    const double Step = FMath::Min(Speed * Dt, FVector::Dist2D(Start, Behavior.Destination));
    FVector Ground;
    const FVector Next = Start + Direction * Step;
    if (!InsideWalkArea(Next, Behavior.Origin) ||
        !ValidateGround(RegionId, Next, *Preset, SituationId, Ground, Error))
    {
        Behavior.bWalking = false;
        Behavior.PauseSeconds = 3;
        verify(Person->ApplyMotion(false));
        return;
    }
    Person->SetActorLocation(Ground);
    if (FVector::Dist2D(Ground, Behavior.Destination) < 1)
    {
        Behavior.bWalking = false;
        Behavior.PauseSeconds = Random.FRandRange(2.f, 5.f);
        Behavior.Seed = Random.GetCurrentSeed();
        verify(Person->ApplyMotion(false));
    }
}

TSharedRef<FJsonObject> AScenarioManager::BehaviorJson() const
{
    auto Json = MakeShared<FJsonObject>();
    Json->SetObjectField(TEXT("origin"), PointJson(Behavior.Origin));
    Json->SetObjectField(TEXT("destination"), PointJson(Behavior.Destination));
    Json->SetNumberField(TEXT("seed"), Behavior.Seed);
    Json->SetNumberField(TEXT("pauseSeconds"), Behavior.PauseSeconds);
    Json->SetNumberField(TEXT("animationTime"),
                         bBehaviorActive ? Person->AnimationTime() : Behavior.AnimationTime);
    Json->SetBoolField(TEXT("walking"), Behavior.bWalking);
    return Json;
}

bool AScenarioManager::ReadBehavior(const TSharedPtr<FJsonObject> &Json, const FVector &Position,
                                    FScenarioBehavior &Result, FString &Error) const
{
    double Seed;
    if (!Json || Json->Values.Num() != 6 || !ReadPoint(Json, TEXT("origin"), Result.Origin) ||
        !ReadPoint(Json, TEXT("destination"), Result.Destination) ||
        !Json->TryGetNumberField(TEXT("seed"), Seed) || !FMath::IsFinite(Seed) || Seed < MIN_int32 ||
        Seed > MAX_int32 || FMath::FloorToDouble(Seed) != Seed ||
        !Json->TryGetNumberField(TEXT("pauseSeconds"), Result.PauseSeconds) ||
        !FMath::IsFinite(Result.PauseSeconds) || Result.PauseSeconds < 0 || Result.PauseSeconds > 5 ||
        !Json->TryGetNumberField(TEXT("animationTime"), Result.AnimationTime) ||
        !FMath::IsFinite(Result.AnimationTime) || Result.AnimationTime < 0 || Result.AnimationTime > 60 ||
        !Json->TryGetBoolField(TEXT("walking"), Result.bWalking) ||
        !InsideWalkArea(Position, Result.Origin) || !InsideWalkArea(Result.Destination, Result.Origin))
    {
        Error = TEXT("Invalid saved character behavior");
        return false;
    }
    Result.Seed = static_cast<int32>(Seed);
    return true;
}
