// Unmodified excerpts from Mesa bffd91947efc1932da99aca69fc540d6d4201b0c.
/*
 * Copyright © 2016 Red Hat.
 * Copyright © 2016 Bas Nieuwenhuizen
 * SPDX-License-Identifier: MIT
 *
 * based in part on anv driver which is:
 * Copyright © 2015 Intel Corporation
 */


   return available_ram;
}

static inline VkDeviceSize
tu_get_budget_memory(struct tu_physical_device *physical_device, uint64_t heap_used)
{
   uint64_t heap_size = physical_device->heap.size;

   /*
    * Let's not incite the app to starve the system: report at most 90% of
    * available system memory.
    */
   const float percent = 0.9f;

   return vk_physical_device_heap_budget_from_system(&physical_device->vk,
                                                     percent, heap_size,
                                                     heap_used);
}

VKAPI_ATTR void VKAPI_CALL
tu_GetPhysicalDeviceMemoryProperties2(VkPhysicalDevice pdev,
                                      VkPhysicalDeviceMemoryProperties2 *props2)
{
   VK_FROM_HANDLE(tu_physical_device, physical_device, pdev);

   VkPhysicalDeviceMemoryProperties *props = &props2->memoryProperties;
   props->memoryHeapCount = 1;
   props->memoryHeaps[0].size = physical_device->heap.size;
   props->memoryHeaps[0].flags = physical_device->heap.flags;

   props->memoryTypeCount = physical_device->memory.type_count;
   for (uint32_t i = 0; i < physical_device->memory.type_count; i++) {
      props->memoryTypes[i] = (VkMemoryType) {
         .propertyFlags = physical_device->memory.types[i],
         .heapIndex     = 0,
      };
   }

   vk_foreach_struct(sType, ext, props2->pNext)
   {
      switch (sType) {
      case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_BUDGET_PROPERTIES_EXT: {
         VkPhysicalDeviceMemoryBudgetPropertiesEXT *memory_budget_props =
            (VkPhysicalDeviceMemoryBudgetPropertiesEXT *) ext;
         memory_budget_props->heapUsage[0] = p_atomic_read(&physical_device->heap.used);
         memory_budget_props->heapBudget[0] = tu_get_budget_memory(physical_device, memory_budget_props->heapUsage[0]);

         /* The heapBudget and heapUsage values must be zero for array elements
          * greater than or equal to VkPhysicalDeviceMemoryProperties::memoryHeapCount
          */
         for (unsigned i = 1; i < VK_MAX_MEMORY_HEAPS; i++) {
            memory_budget_props->heapBudget[i] = 0u;
            memory_budget_props->heapUsage[i] = 0u;
         }
         break;
      }
      default:
         break;
      }
   }
}

static VkResult
tu_add_to_heap(struct tu_device *dev, struct tu_bo *bo)
{
   struct tu_memory_heap *mem_heap = &dev->physical_device->heap;
   uint64_t mem_heap_used = p_atomic_add_return(&mem_heap->used, bo->size);
   if (mem_heap_used > mem_heap->size) {
      p_atomic_add(&mem_heap->used, -bo->size);
      tu_bo_finish(dev, bo);
      return vk_errorf(dev, VK_ERROR_OUT_OF_DEVICE_MEMORY,
                       "Out of heap memory");
   }
   return VK_SUCCESS;
}

static void
_tu_destroy_memory(struct tu_device *device,
                  struct tu_device_memory *mem)
{
   if (mem->bo) {
      p_atomic_add(&device->physical_device->heap.used, -mem->bo->size);
      tu_bo_finish(device, mem->bo);
   }

   if (mem->lazy) {
      tu_sparse_vma_finish(device, &mem->lazy_vma);
      mtx_destroy(&mem->lazy_mutex);
   }
}
