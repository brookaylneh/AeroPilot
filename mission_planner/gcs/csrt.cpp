#include <cstdint>
#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>
#include <opencv2/tracking.hpp>

namespace {
cv::Ptr<cv::TrackerCSRT> tracker;
cv::Mat image;
cv::Rect box;

void pixels(const uint8_t* rgba, int width, int height) {
    CV_Assert(rgba != nullptr && width == 960 && height == 540);
    cv::cvtColor(cv::Mat(height, width, CV_8UC4, const_cast<uint8_t*>(rgba)),
                 image, cv::COLOR_RGBA2BGR);
}
}

extern "C" {
void csrt_init(const uint8_t* rgba, int width, int height, int x, int y, int w, int h) {
    CV_Assert(x >= 0 && y >= 0 && w > 0 && h > 0 && x + w <= width && y + h <= height);
    pixels(rgba, width, height);
    cv::setNumThreads(1);
    cv::TrackerCSRT::Params parameters;
    parameters.template_size = 128;
    parameters.number_of_scales = 17;
    tracker = cv::TrackerCSRT::create(parameters);
    box = cv::Rect(x, y, w, h);
    tracker->init(image, box);
}

int csrt_update(const uint8_t* rgba, int width, int height, int32_t* output) {
    CV_Assert(tracker && output != nullptr);
    pixels(rgba, width, height);
    if (!tracker->update(image, box)) return 0;
    output[0] = box.x;
    output[1] = box.y;
    output[2] = box.x + box.width;
    output[3] = box.y + box.height;
    return 1;
}

void csrt_reset() {
    tracker.release();
    image.release();
}
}
