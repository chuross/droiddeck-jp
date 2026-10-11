/* Small pre-game check; build with a recent MinGW-w64 toolchain:
 * x86_64-w64-mingw32-gcc -Wall -Wextra dxgi-memory-probe.c -ldxgi -ldxguid -o dxgi-memory-probe.exe
 * Use the same patched Proton, prefix, driver and DLL overrides for both runs:
 * DXVK_CONFIG="dxgi.maxDeviceMemory = 0; dxgi.maxSharedMemory = 0" <proton> run dxgi-memory-probe.exe
 * DXVK_CONFIG="dxgi.maxDeviceMemory = 2048; dxgi.maxSharedMemory = 2048" <proton> run dxgi-memory-probe.exe
 * WINEDEBUG=+loaddll confirms the normal GE/DXVK native DXGI loading path.
 * Run in the same ARM64 Proton environment as the game.
 * Compare only originally larger fields; usage/reservation are live values
 * and may differ between runs. This is a report clamp, not a RAM hard limit.
 */
#define COBJMACROS
#include <windows.h>
#include <dxgi1_6.h>
#include <stdio.h>

static void memory_info(IDXGIAdapter3 *adapter, DXGI_MEMORY_SEGMENT_GROUP group, const char *name)
{
    DXGI_QUERY_VIDEO_MEMORY_INFO info;
    HRESULT hr = IDXGIAdapter3_QueryVideoMemoryInfo(adapter, 0, group, &info);
    if (FAILED(hr))
    {
        printf("%s QueryVideoMemoryInfo failed: %#lx\n", name, (unsigned long)hr);
        return;
    }
    printf("%s Budget=%llu CurrentUsage=%llu CurrentReservation=%llu AvailableForReservation=%llu bytes\n",
            name, (unsigned long long)info.Budget, (unsigned long long)info.CurrentUsage,
            (unsigned long long)info.CurrentReservation, (unsigned long long)info.AvailableForReservation);
}

int main(void)
{
    IDXGIFactory *factory;
    IDXGIAdapter *adapter;
    UINT index;
    HRESULT hr = CreateDXGIFactory(&IID_IDXGIFactory, (void **)&factory);
    if (FAILED(hr))
    {
        printf("CreateDXGIFactory failed: %#lx\n", (unsigned long)hr);
        return 1;
    }
    for (index = 0; ; ++index)
    {
        DXGI_ADAPTER_DESC desc;
        IDXGIAdapter3 *adapter3;
        IDXGIAdapter4 *adapter4;
        hr = IDXGIFactory_EnumAdapters(factory, index, &adapter);
        if (hr == DXGI_ERROR_NOT_FOUND) break;
        if (FAILED(hr))
        {
            printf("EnumAdapters failed: %#lx\n", (unsigned long)hr);
            IDXGIFactory_Release(factory);
            return 1;
        }
        printf("Adapter %u\n", index);
        hr = IDXGIAdapter_GetDesc(adapter, &desc);
        if (SUCCEEDED(hr))
            printf("GetDesc LUID=%08lx:%08lx DedicatedVideoMemory=%llu SharedSystemMemory=%llu bytes\n",
                    (unsigned long)desc.AdapterLuid.HighPart, (unsigned long)desc.AdapterLuid.LowPart,
                    (unsigned long long)desc.DedicatedVideoMemory, (unsigned long long)desc.SharedSystemMemory);
        else printf("GetDesc failed: %#lx\n", (unsigned long)hr);
        hr = IDXGIAdapter_QueryInterface(adapter, &IID_IDXGIAdapter4, (void **)&adapter4);
        if (SUCCEEDED(hr))
        {
            DXGI_ADAPTER_DESC3 desc3;
            hr = IDXGIAdapter4_GetDesc3(adapter4, &desc3);
            if (SUCCEEDED(hr))
                printf("GetDesc3 DedicatedVideoMemory=%llu SharedSystemMemory=%llu bytes\n",
                        (unsigned long long)desc3.DedicatedVideoMemory, (unsigned long long)desc3.SharedSystemMemory);
            else printf("GetDesc3 failed: %#lx\n", (unsigned long)hr);
            IDXGIAdapter4_Release(adapter4);
        }
        else printf("IDXGIAdapter4 unavailable: %#lx\n", (unsigned long)hr);
        hr = IDXGIAdapter_QueryInterface(adapter, &IID_IDXGIAdapter3, (void **)&adapter3);
        if (SUCCEEDED(hr))
        {
            memory_info(adapter3, DXGI_MEMORY_SEGMENT_GROUP_LOCAL, "LOCAL");
            memory_info(adapter3, DXGI_MEMORY_SEGMENT_GROUP_NON_LOCAL, "NON_LOCAL");
            IDXGIAdapter3_Release(adapter3);
        }
        else printf("IDXGIAdapter3 unavailable: %#lx\n", (unsigned long)hr);
        IDXGIAdapter_Release(adapter);
    }
    IDXGIFactory_Release(factory);
    return index ? 0 : 1;
}

