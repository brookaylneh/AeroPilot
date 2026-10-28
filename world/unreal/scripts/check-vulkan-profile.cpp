#include <cstdio>
#include <cstdlib>
#include <vector>

#include <vulkan/vulkan.h>

// UE's generated profile header calls Vulkan through this namespace.
namespace VulkanRHI
{
using ::vkCreateDevice;
using ::vkCreateInstance;
using ::vkEnumerateDeviceExtensionProperties;
using ::vkEnumerateInstanceExtensionProperties;
using ::vkEnumerateInstanceVersion;
using ::vkGetDeviceProcAddr;
using ::vkGetInstanceProcAddr;
using ::vkGetPhysicalDeviceFeatures2;
using ::vkGetPhysicalDeviceFormatProperties2;
using ::vkGetPhysicalDeviceProperties2;
using ::vkGetPhysicalDeviceQueueFamilyProperties2;
}

void ProfileMessage(const char *Message)
{
    std::fprintf(stderr, "%s\n", Message);
}
#define VP_DEBUG_MESSAGE_CALLBACK ProfileMessage
#include "vulkan_profiles_ue.h"

static void Require(VkResult Result, const char *Operation)
{
    if (Result != VK_SUCCESS)
    {
        std::fprintf(stderr, "%s failed: VkResult %d\n", Operation, Result);
        std::exit(EXIT_FAILURE);
    }
}

int main()
{
    const VpProfileProperties Profiles[] = {
        {VP_UE_VULKAN_SM6_NAME, VP_UE_VULKAN_SM6_SPEC_VERSION},
        {VP_UE_VULKAN_SM6_RT_NAME, VP_UE_VULKAN_SM6_RT_SPEC_VERSION},
    };
    bool Supported = true;
    for (const auto &Profile : Profiles)
    {
        VkBool32 InstanceSupported = VK_FALSE;
        Require(vpGetInstanceProfileSupport(nullptr, &Profile, &InstanceSupported),
                "vpGetInstanceProfileSupport");
        std::printf("%s instance support: %u\n", Profile.profileName, InstanceSupported);
        if (!InstanceSupported)
            return EXIT_FAILURE;
        const VkInstanceCreateInfo CreateInfo{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
        VpInstanceCreateInfo ProfileInfo{};
        ProfileInfo.pCreateInfo = &CreateInfo;
        ProfileInfo.enabledFullProfileCount = 1;
        ProfileInfo.pEnabledFullProfiles = &Profile;
        VkInstance Instance = VK_NULL_HANDLE;
        Require(vpCreateInstance(&ProfileInfo, nullptr, &Instance), "vpCreateInstance");
        uint32_t Count = 0;
        Require(vkEnumeratePhysicalDevices(Instance, &Count, nullptr), "vkEnumeratePhysicalDevices");
        if (Count != 1)
        {
            std::fprintf(stderr, "Expected one NVIDIA device, found %u\n", Count);
            vkDestroyInstance(Instance, nullptr);
            return EXIT_FAILURE;
        }
        std::vector<VkPhysicalDevice> Devices(Count);
        Require(vkEnumeratePhysicalDevices(Instance, &Count, Devices.data()), "vkEnumeratePhysicalDevices");
        VkPhysicalDeviceProperties Properties{};
        vkGetPhysicalDeviceProperties(Devices[0], &Properties);
        VkBool32 DeviceSupported = VK_FALSE;
        Require(vpGetPhysicalDeviceProfileSupport(Instance, Devices[0], &Profile, &DeviceSupported),
                "vpGetPhysicalDeviceProfileSupport");
        std::printf("%s: device=%s vendor=0x%x api=%u.%u.%u supported=%u\n", Profile.profileName,
                    Properties.deviceName, Properties.vendorID, VK_API_VERSION_MAJOR(Properties.apiVersion),
                    VK_API_VERSION_MINOR(Properties.apiVersion), VK_API_VERSION_PATCH(Properties.apiVersion),
                    DeviceSupported);
        Supported &= Properties.vendorID == 0x10de && DeviceSupported;
        vkDestroyInstance(Instance, nullptr);
    }
    return Supported ? EXIT_SUCCESS : EXIT_FAILURE;
}
