#include "ScenarioSession.h"
#include "ScenarioManager.h"
#include "Camera/CameraActor.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Engine/TextureRenderTarget2D.h"
#include "Dom/JsonObject.h"
#include "IImageWrapper.h"
#include "IImageWrapperModule.h"
#include "Misc/Base64.h"
#include "Modules/ModuleManager.h"
#include "ImageUtils.h"

bool AScenarioSession::CapturePortrait(FString &Error)
{
    if (!SetCamera(TEXT("portrait"), Error))
        return false;
    auto *Target = NewObject<UTextureRenderTarget2D>(this);
    // Supersample the still portrait; it has no temporal accumulation history.
    Target->InitCustomFormat(768, 960, PF_B8G8R8A8, false);
    Target->TargetGamma = 2.2f;
    auto *Capture = NewObject<USceneCaptureComponent2D>(Camera);
    Capture->bCaptureEveryFrame = false;
    Capture->bCaptureOnMovement = false;
    Capture->bAlwaysPersistRenderingState = true;
    // A one-shot capture has no exposure history. Match the world's EV13
    // daylight exposure explicitly, including its 18% middle-grey calibration.
    Capture->PostProcessSettings.bOverride_AutoExposureMethod = true;
    Capture->PostProcessSettings.AutoExposureMethod = AEM_Manual;
    Capture->PostProcessSettings.bOverride_AutoExposureApplyPhysicalCameraExposure = true;
    Capture->PostProcessSettings.AutoExposureApplyPhysicalCameraExposure = false;
    Capture->PostProcessSettings.bOverride_AutoExposureBias = true;
    Capture->PostProcessSettings.AutoExposureBias = -13 + FMath::Log2(.18f);
    Capture->CaptureSource = ESceneCaptureSource::SCS_FinalColorLDR;
    // The portrait may be refreshed after placement. Exclude the world so its
    // background cannot disclose the private target location to the briefing.
    Capture->PrimitiveRenderMode = ESceneCapturePrimitiveRenderMode::PRM_UseShowOnlyList;
    Capture->ShowOnlyActorComponents(Manager->Person, true);
    Capture->ShowFlags.SetAtmosphere(false);
    Capture->ShowFlags.SetFog(false);
    Capture->TextureTarget = Target;
    Capture->FOVAngle = 22;
    Capture->SetWorldTransform(Camera->GetActorTransform());
    Capture->RegisterComponent();
    Capture->CaptureScene();
    TArray<FColor> Pixels;
    const bool Read = Target->GameThread_GetRenderTargetResource()->ReadPixels(Pixels);
    Capture->DestroyComponent();
    if (!Read || Pixels.Num() != 768 * 960)
    {
        Error = TEXT("Unreal could not capture the character portrait");
        return false;
    }
    auto &Images = FModuleManager::LoadModuleChecked<IImageWrapperModule>(TEXT("ImageWrapper"));
    const auto Encoder = Images.CreateImageWrapper(EImageFormat::JPEG);
    TArray<FColor> PortraitPixels;
    FImageUtils::ImageResize(768, 960, Pixels, 256, 320, PortraitPixels, true);
    if (!Encoder->SetRaw(PortraitPixels.GetData(), PortraitPixels.Num() * sizeof(FColor), 256, 320,
                         ERGBFormat::BGRA, 8))
    {
        Error = TEXT("Could not encode the character portrait");
        return false;
    }
    const auto &Bytes = Encoder->GetCompressed(85);
    const FString Portrait = TEXT("data:image/jpeg;base64,") + FBase64::Encode(Bytes.GetData(), Bytes.Num());
    if (Bytes.IsEmpty() || Portrait.Len() > 45000)
    {
        Error = TEXT("Character portrait exceeds the supported image size");
        return false;
    }
    Briefing->SetStringField(TEXT("portrait"), Portrait);
    return true;
}
