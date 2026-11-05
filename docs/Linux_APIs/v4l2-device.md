# Kernel Module v4l2-device.md API Documentation

## 2.5.V4L2 device instance¶

## 2.5.1.v4l2_device functions and data structures¶

| 名称 | 描述 |
|---|---|
| struct v4l2_device | main struct to for V4L2 device drivers |
| v4l2_device_get | gets a V4L2 device reference |
| v4l2_device_put | puts a V4L2 device reference |
| v4l2_device_register | Initialize v4l2_dev and makedev->driver_datapoint tov4l2_dev. |
| v4l2_device_set_name | Optional function to initialize the name field of structv4l2_device |
| v4l2_device_disconnect | Change V4L2 device state to disconnected. |
| v4l2_device_unregister | Unregister all sub-devices and any other resources related tov4l2_dev. |
| v4l2_device_register_subdev | Registers a subdev with a v4l2 device. |
| v4l2_device_unregister_subdev | Unregisters a subdev with a v4l2 device. |
| __v4l2_device_register_subdev_nodes | Registers device nodes for all subdevs of the v4l2 device that are marked with theV4L2_SUBDEV_FL_HAS_DEVNODEflag. |
| v4l2_device_register_subdev_nodes | Registers subdevices device nodes with unrestricted access to the subdevice userspace operations |
| v4l2_device_register_ro_subdev_nodes | Registers subdevices device nodes in read-only mode |
| v4l2_subdev_notify | Sends a notification to v4l2_device. |
| v4l2_device_supports_requests | Test if requests are supported. |
| v4l2_device_for_each_subdev | Helper macro that interates over all sub-devices of a givenv4l2_device. |
| __v4l2_device_call_subdevs_p | Calls the specified operation for all subdevs matching the condition. |
| __v4l2_device_call_subdevs | Calls the specified operation for all subdevs matching the condition. |
| __v4l2_device_call_subdevs_until_err_p | Calls the specified operation for all subdevs matching the condition. |
| __v4l2_device_call_subdevs_until_err | Calls the specified operation for all subdevs matching the condition. |
| v4l2_device_call_all | Calls the specified operation for all subdevs matching thev4l2_subdev.grp_id, as assigned by the bridge driver. |
| v4l2_device_call_until_err | Calls the specified operation for all subdevs matching thev4l2_subdev.grp_id, as assigned by the bridge driver, until an error occurs. |
| v4l2_device_mask_call_all | Calls the specified operation for all subdevices where a group ID matches a specified bitmask. |
| v4l2_device_mask_call_until_err | Calls the specified operation for all subdevices where a group ID matches a specified bitmask. |
| v4l2_device_has_op | checks if any subdev with matching grpid has a given ops. |
| v4l2_device_mask_has_op | checks if any subdev with matching group mask has a given ops. |

## 总数统计

总共提取了 25 个函数+结构。
