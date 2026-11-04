#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
test "$#" = 0
csrt_root="$PWD/.local/vendor/csrt"
csrt_builder=emscripten/emsdk@sha256:a424cb920e13a48547f71a87b992018d161e7415db984c88bcb99f9efa5e4e7e
mkdir "$csrt_root"
curl --fail --location --retry 0 --max-time 120 \
  https://codeload.github.com/opencv/opencv/tar.gz/refs/tags/4.12.0 -o "$csrt_root/opencv.tar.gz"
curl --fail --location --retry 0 --max-time 120 \
  https://codeload.github.com/opencv/opencv_contrib/tar.gz/refs/tags/4.12.0 -o "$csrt_root/contrib.tar.gz"
(
  cd "$csrt_root"
  sha256sum --check <<'CHECKSUMS'
44c106d5bb47efec04e531fd93008b3fcd1d27138985c5baf4eafac0e1ec9e9d  opencv.tar.gz
4197722b4c5ed42b476d42e29beb29a52b6b25c34ec7b4d589c3ae5145fee98e  contrib.tar.gz
CHECKSUMS
)
mkdir "$csrt_root/opencv" "$csrt_root/contrib" "$csrt_root/runtime"
tar -xzf "$csrt_root/opencv.tar.gz" --strip-components=1 -C "$csrt_root/opencv"
tar -xzf "$csrt_root/contrib.tar.gz" --strip-components=1 -C "$csrt_root/contrib"
docker run --rm --network none --cpus 4 --memory 8g --pids-limit 512 \
  --env NVIDIA_VISIBLE_DEVICES=void \
  --mount "type=bind,src=$csrt_root,dst=/work" \
  --mount "type=bind,src=$PWD/mission_planner/gcs/csrt.cpp,dst=/csrt.cpp,readonly" \
  "$csrt_builder" bash -euo pipefail -c '
    emcmake cmake -S /work/opencv -B /work/build \
      -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_FLAGS="-O3 -msimd128" \
      -DCMAKE_CXX_FLAGS="-O3 -msimd128 -fexceptions" \
      -DOPENCV_EXTRA_MODULES_PATH=/work/contrib/modules \
      -DBUILD_LIST=core,imgproc,video,tracking -DBUILD_SHARED_LIBS=OFF \
      -DBUILD_TESTS=OFF -DBUILD_PERF_TESTS=OFF -DBUILD_EXAMPLES=OFF -DBUILD_opencv_apps=OFF \
      -DBUILD_opencv_js=OFF -DBUILD_opencv_python2=OFF -DBUILD_opencv_python3=OFF \
      -DWITH_IPP=OFF -DWITH_ITT=OFF -DWITH_OPENCL=OFF -DWITH_OPENMP=OFF \
      -DWITH_PTHREADS_PF=OFF -DWITH_TBB=OFF -DWITH_LAPACK=OFF -DWITH_EIGEN=OFF \
      -DWITH_PROTOBUF=OFF -DWITH_QUIRC=OFF -DCV_TRACE=OFF -DCPU_BASELINE="" -DCPU_DISPATCH=""
    cmake --build /work/build --parallel 4
    em++ /csrt.cpp -std=c++17 -O3 -msimd128 -fexceptions \
      -I/work/build -I/work/opencv/modules/core/include -I/work/opencv/modules/imgproc/include \
      -I/work/opencv/modules/video/include -I/work/contrib/modules/tracking/include \
      -L/work/build/lib -lopencv_tracking -lopencv_video -lopencv_imgproc -lopencv_core \
      /work/build/3rdparty/lib/libzlib.a \
      -sMODULARIZE=1 -sEXPORT_ES6=1 -sENVIRONMENT=web,worker,node \
      -sALLOW_MEMORY_GROWTH=1 -sINITIAL_MEMORY=33554432 -sMAXIMUM_MEMORY=268435456 \
      -sDISABLE_EXCEPTION_CATCHING=0 -sFILESYSTEM=0 \
      -sEXPORTED_FUNCTIONS=_malloc,_free,_csrt_init,_csrt_update,_csrt_reset \
      -sEXPORTED_RUNTIME_METHODS=HEAPU8,HEAP32 \
      -o /work/runtime/csrt.js
    cp /work/opencv/LICENSE /work/runtime/OPENCV-LICENSE.txt
    sha256sum /work/runtime/csrt.js /work/runtime/csrt.wasm
  '
sudo -n chown -R "$(id -u):$(id -g)" "$csrt_root"
