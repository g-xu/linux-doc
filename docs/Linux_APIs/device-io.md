# Kernel Module device-io.md API Documentation

## Bus-Independent Device Accesses

## Public Functions Provided¶

| 名称 | 描述 |
|---|---|
| virt_to_phys | map virtual addresses to physical |
| phys_to_virt | map physical address to virtual |
| ioremap | map bus memory into CPU space |
| iosubmit_cmds512 | copy data to single MMIO location, in 512-bit units |
| pci_iomap_range | create a virtual mapping cookie for a PCI BAR |
| pci_iomap_wc_range | create a virtual WC mapping cookie for a PCI BAR |
| pci_iomap | create a virtual mapping cookie for a PCI BAR |
| pci_iomap_wc | create a virtual WC mapping cookie for a PCI BAR |

## 总数统计

总共提取了 8 个函数+结构。
