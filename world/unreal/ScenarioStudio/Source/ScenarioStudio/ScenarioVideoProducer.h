#pragma once
#include "IPixelStreaming2VideoProducer.h"
#include "PixelCaptureInputFrameRHI.h"
#include "Framework/Application/SlateApplication.h"
#include "Rendering/SlateRenderer.h"
#include "Slate/SlateViewportProvider.h"
#include "Async/Async.h"

// Offscreen development runs can have utility windows. Capture only the game
// window so their different dimensions cannot repeatedly reset the encoder.
class FScenarioVideoProducer final : public IPixelStreaming2VideoProducer,
                                     public TSharedFromThis<FScenarioVideoProducer>
{
  public:
    static TSharedPtr<FScenarioVideoProducer> Create(TSharedPtr<SWindow> Window)
    {
        if (!Window || !FSlateApplication::IsInitialized())
            return nullptr;
        TSharedPtr<FScenarioVideoProducer> Result = MakeShareable(new FScenarioVideoProducer());
        Result->GameWindow = Window;
        Result->Handle = FSlateApplication::Get().GetRenderer()->OnBackBufferReadyToPresent().AddSP(
            Result.ToSharedRef(), &FScenarioVideoProducer::Capture);
        return Result;
    }
    ~FScenarioVideoProducer()
    {
        if (!IsEngineExitRequested())
            AsyncTask(ENamedThreads::GameThread, [Copy = Handle]() {
                if (FSlateApplication::IsInitialized())
                    FSlateApplication::Get().GetRenderer()->OnBackBufferReadyToPresent().Remove(Copy);
            });
    }
    virtual EVideoProducerCapabilities GetCapabilities() override
    {
        return EVideoProducerCapabilities::Default;
    }
    virtual FString ToString() override
    {
        return TEXT("Scenario game window");
    }

  private:
    TWeakPtr<SWindow> GameWindow;
    FDelegateHandle Handle;
    void Capture(SWindow &Window, ISlateViewportProvider &Provider)
    {
        if (GameWindow.Pin().Get() == &Window)
            PushFrame(FPixelCaptureInputFrameRHI(Provider.GetBackBufferResource()));
    }
};
