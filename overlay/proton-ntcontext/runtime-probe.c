/* Headless x64/ARM64EC loader check; no Vulkan device or Steam login required. */
#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>

typedef LONG (WINAPI *get_context_fn)(HANDLE, CONTEXT *);
static DWORD WINAPI worker(void *unused) { (void)unused; return 0; }

int main(int argc, char **argv)
{
    const char *exports[] = {"CreateDXGIFactory", "D3D12GetInterface"};
    get_context_fn get_context;
    HANDLE thread;
    CONTEXT context;
    LONG status;
    HMODULE modules[2];
    int i;
    if (argc != 4) return 1;
    get_context = (get_context_fn)(void *)GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "NtGetContextThread");
    if (!get_context) return 2;
    thread = CreateThread(NULL, 0, worker, NULL, CREATE_SUSPENDED, NULL);
    if (!thread) return 3;
    memset(&context, 0, sizeof(context));
    context.ContextFlags = CONTEXT_ALL;
    status = get_context(thread, &context);
    ResumeThread(thread);
    WaitForSingleObject(thread, INFINITE);
    CloseHandle(thread);
    if (status) { printf("valid context failed: %#lx\n", (unsigned long)status); return 4; }
    if (!strcmp(argv[1], "patched") && (uint32_t)get_context(GetCurrentThread(), NULL) != 0xc000000du)
        return 5;
    for (i = 0; i < 2; ++i)
    {
        WCHAR path[32768];
        HMODULE module;
        if (!MultiByteToWideChar(CP_UTF8, 0, argv[i + 2], -1, path, 32768)) return 6;
        module = LoadLibraryW(path);
        if (!module) { printf("load failed: %s error=%lu\n", argv[i + 2], GetLastError()); return 7; }
        if (!GetProcAddress(module, exports[i])) return 8;
        modules[i] = module;
    }
    for (i = 1; i >= 0; --i) FreeLibrary(modules[i]);
    printf("DROIDDECK_RUNTIME_OK %s\n", argv[1]);
    return 0;
}
