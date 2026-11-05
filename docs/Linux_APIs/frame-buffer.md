# Kernel Module frame-buffer.md API Documentation

## Frame Buffer Library¶

## Frame Buffer Memory¶

| 名称 | 描述 |
|---|---|
| remove_conflicting_framebuffers | remove firmware-configured framebuffers |
| remove_conflicting_pci_framebuffers | remove firmware-configured framebuffers for PCI devices |
| register_framebuffer | registers a frame buffer device |
| unregister_framebuffer | releases a frame buffer device |
| fb_set_suspend | low level driver signals suspend |

## Frame Buffer Colormap¶

| 名称 | 描述 |
|---|---|
| fb_dealloc_cmap | deallocate a colormap |
| fb_copy_cmap | copy a colormap |
| fb_set_cmap | set the colormap |
| fb_default_cmap | get default colormap |
| fb_invert_cmaps | invert all defaults colormaps |

## Frame Buffer Video Mode Database¶

| 名称 | 描述 |
|---|---|
| fb_try_mode | test a video mode |
| fb_delete_videomode | removed videomode entry from modelist |
| fb_find_mode | finds a valid video mode |
| fb_var_to_videomode | convert fb_var_screeninfo to fb_videomode |
| fb_videomode_to_var | convert fb_videomode to fb_var_screeninfo |
| fb_mode_is_equal | compare 2 videomodes |
| fb_find_best_mode | find best matching videomode |
| fb_find_nearest_mode | find closest videomode |
| fb_match_mode | find a videomode which exactly matches the timings in var |
| fb_add_videomode | adds videomode entry to modelist |
| fb_destroy_modelist | destroy modelist |
| fb_videomode_to_modelist | convert mode array to mode list |

## Frame Buffer Macintosh Video Mode Database¶

| 名称 | 描述 |
|---|---|
| mac_vmode_to_var | converts vmode/cmode pair to var structure |
| mac_map_monitor_sense | Convert monitor sense to vmode |
| mac_find_mode | find a video mode |

## Frame Buffer Fonts¶

## 总数统计

总共提取了 25 个函数+结构。
