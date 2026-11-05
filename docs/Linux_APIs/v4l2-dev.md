# Kernel Module v4l2-dev.md API Documentation

## 2.4.Video device’ s internal representation¶

## 2.4.1.ioctls and locking¶

## 2.4.2.Video device registration¶

## 2.4.3.video device debugging¶

## 2.4.4.Video device cleanup¶

## 2.4.5.helper functions¶

## 2.4.6.video_device functions and data structures¶

| 名称 | 描述 |
|---|---|
| struct vfl_devnode_type | type of V4L2 device node |
| struct vfl_devnode_direction | Identifies if astructvideo_devicecorresponds to a receiver, a transmitter or a mem-to-mem device. |
| struct v4l2_video_device_flags | Flags used bystructvideo_device |
| struct v4l2_prio_state | stores the priority states |
| v4l2_prio_init | initializes astructv4l2_prio_state |
| v4l2_prio_change | changes the v4l2 file handler priority |
| v4l2_prio_open | Implements the priority logic for a file handler open |
| v4l2_prio_close | Implements the priority logic for a file handler close |
| v4l2_prio_max | Return the maximum priority, as stored at theglobalarray. |
| v4l2_prio_check | Implements the priority logic for a file handler close |
| struct v4l2_file_operations | fs operations used by a V4L2 device |
| struct video_device | Structure used to create and manage the V4L2 device nodes. |
| media_entity_to_video_device | Returns astructvideo_devicefrom thestructmedia_entityembedded on it. |
| to_video_device | Returns astructvideo_devicefrom thestructdeviceembedded on it. |
| __video_register_device | register video4linux devices |
| video_register_device | register video4linux devices |
| video_register_device_no_warn | register video4linux devices |
| video_unregister_device | Unregister video devices. |
| video_device_alloc | helper function to allocstructvideo_device |
| video_device_release | helper function to releasestructvideo_device |
| video_device_release_empty | helper function to implement the video_device->release() callback. |
| v4l2_disable_ioctl | mark that a given command isn’t implemented. shouldn’t use core locking |
| video_get_drvdata | gets private data fromstructvideo_device. |
| video_set_drvdata | sets private data fromstructvideo_device. |
| video_devdata | getsstructvideo_devicefrom struct file. |
| video_drvdata | gets private data fromstructvideo_deviceusing the struct file. |
| video_device_node_name | returns the video device name |
| video_is_registered | returns true if thestructvideo_deviceis registered. |

## 总数统计

总共提取了 28 个函数+结构。
