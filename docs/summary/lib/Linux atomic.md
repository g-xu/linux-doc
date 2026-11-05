
## 参考
ARM64-linux并发与同步之原子操作
02-Linux-Kernel-armv8-aarch64-的原子操作的底层实现
Linux-Kernel-atomic-原子操作-官方文档
## APIs
atomic有三种类型：atomic_t/atomic64_t/atomic_long_t，下面的API对三种类型都是类似的，只是分别以atomic/atomic64/atomic-long开头

| API                                                                                                          | 描述                 |
| ------------------------------------------------------------------------------------------------------------ | ------------------ |
| atomic_read<br>atomic_read_acquire                                                                           | 获取原子变量值            |
| atomic_set<br>atomic_set_release                                                                             | 设置原子变量值            |
| atomic_{add,sub,inc,dec}                                                                                     | 增加减少变量值            |
| `atomic_{add,sub,inc,dec}_return{,_relaxed,_acquire,_release}`                                               | 带内存屏障的操作，返回新值      |
| `atomic_fetch_{add,sub,inc,dec}{,_relaxed,_acquire,_release}`                                                | 带内存屏障的操作，返回旧值      |
| atomic_{and,or,xor,andnot}                                                                                   | 位操作                |
| `atomic_fetch_{and,or,xor,andnot}{,_relaxed,_acquire,_release}`                                              | 带内存屏障的操作，返回旧值      |
| atomic_xchg{,_relaxed,_acquire,_release}                                                                     | 修改内容               |
| `atomic_cmpxchg{,_relaxed,_acquire,_release}`                                                                | 比较，满足条件修改，返回旧值     |
| `atomic_try_cmpxchg{,_relaxed,_acquire,_release}`                                                            | 比较，满足条件修改，返回成功或者失败 |
| atomic_add_unless(), atomic_inc_not_zero()<br>atomic_dec_unless_positive(), atomic_inc_unless_negative()<br> | 条件满足时候才做操作         |
| atomic_inc_and_test(), atomic_add_negative()<br>atomic_sub_and_test(), atomic_dec_and_test()                 | 增加/减少，然后判断是否为0或者负数 |
|                                                                                                              |                    |
无返回值的普通操作：atomic_{} ()
==返回修改后值的操作==：`atomic_{}_return()`
这些操作仅限于算术运算，因为它们是可逆的。位操作是不可逆的，因此修改后的值具有不确定的实用性。
==返回原始值的操作==：atomic_fetch_{} ()
交换操作(swap operations)：xchg(), cmpxchg() and try_cmpxchg()
杂项 misc：在给定接口的情况下，通常使用 (try_)cmpxchg 循环来实现常用的特殊用途操作，但这些操作是时间关键的，并且可以（通常）在LL/SC体系结构上更有效地实现。

具体解释

|                                                           |                                                                                      |                                |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------ |
| `void atomic_inc(atomic_t *v)`                            | 自增`1`                                                                                | 无                              |
| `int atomic_inc_return(atomic_t *v)`                      | 自增`1`                                                                                | 返回新值                           |
| `int atomic_fetch_inc(atomic_t *v)`                       | 自增`1`                                                                                | 返回旧值                           |
| `void atomic_add(int i, atomic_t *v)`                     | 自增`i`                                                                                | 无                              |
| `int atomic_add_return(int i, atomic_t *v)`               | 自增`i`                                                                                | 返回新值                           |
| `int atomic_fetch_add(int i, atomic_t *v)`                | 自增`i`                                                                                | 返回旧值                           |
|                                                           |                                                                                      |                                |
| `bool atomic_inc_and_test(atomic_t *v)`                   | 自增`1`                                                                                | 如果新值为0，返回`true`；否则返回`false`    |
|                                                           |                                                                                      |                                |
| `bool atomic_add_negative(int i, atomic_t *v)`            | 自增`i`                                                                                | 如果新值为负数，返回`true`；否则返回`false`   |
|                                                           |                                                                                      |                                |
| `int atomic_fetch_add_unless(atomic_t *v, int a, int u)`  | `unless`表示`if not`。如果旧值跟`u`不等，自增`a`，返回旧值；  <br>如果旧值等于`u`，只返回旧值，不会进行自增操作              | 返回旧值                           |
| `bool atomic_add_unless(atomic_t *v, int a, int u)`       | 如果旧值跟`u`不等，自增`a`，返回`true`；  <br>如果旧值等于`u`，返回旧值`false`，不会进行自增操作                       | 表示是否进行了自增                      |
| `bool atomic_inc_not_zero(atomic_t *v)`                   | 如果旧值不等于0，那么自增`1`，返回`true`；如果旧值等于0，返回`false`                                          | 表示是否进行了自增                      |
| `bool atomic_inc_unless_negative(atomic_t *v)`            | 如果旧值不是负数，那么自增`1`，返回`true`；如果旧值为负数，返回`false`                                          | 表示是否进行了自增                      |
|                                                           |                                                                                      |                                |
| `void atomic_dec(atomic_t *v)`                            | 自减`1`                                                                                | 无                              |
| `int atomic_dec_return(atomic_t *v)`                      | 自减`1`                                                                                | 返回新值                           |
| `int atomic_fetch_dec(atomic_t *v)`                       |                                                                                      |                                |
| `void atomic_sub(int i, atomic_t *v)`                     | 自减`i`                                                                                | 无                              |
| `int atomic_sub_return(int i, atomic_t *v)`               | 自减`i`                                                                                | 返回新值                           |
| `int atomic_fetch_sub(int i, atomic_t *v)`                | 自减`i`                                                                                | 返回旧值                           |
|                                                           |                                                                                      |                                |
| `bool atomic_sub_and_test(int i, atomic_t *v)`            | 自减`i`                                                                                | 如果新值为0，返回`true`；否则返回`false`    |
| `bool atomic_dec_and_test(atomic_t *v)`                   | 自减`1`                                                                                | 如果新值为0，返回`true`；否则返回`false`    |
| `bool atomic_dec_unless_positive(atomic_t *v)`            | 如果旧值不是正数(&lt;=0)，那么自减`1`，返回`true`；如果旧值是正数(&gt;0)，返回`false`                                 | 表示是否进行了自减操作                    |
| `int atomic_xchg(atomic_t *v, int i)`                     | 将`i`赋值给原子变量                                                                          | 返回旧值                           |
| `int atomic_cmpxchg(atomic_t *v, int old, int new)`       | 如果`*v`跟`old`相等，将`new`赋值给`*v`，返回`*v`；  <br>如果旧值跟`old`不等，返回`*v`                        | 返回旧值                           |
| `bool atomic_try_cmpxchg(atomic_t *v, int *old, int new)` | 如果`*v`跟`*old`相等，将`new`赋值给`*v`，返回`true`；  <br>如果旧值跟`*old`不等，将`*v`赋值给给`*old`，返回`false` | 返回的是布尔类型，表示原子变量是否成功赋值          |
| `void atomic_and(int i, atomic_t *v)`                     | `逻辑与`                                                                                | 无                              |
| `int atomic_fetch_and(int i, atomic_t *v)`                | `逻辑与`                                                                                | 返回旧值                           |
| `void atomic_andnot(int i, atomic_t *v)`                  | 跟`~i`进行`逻辑与`                                                                         | 无                              |
| `int atomic_fetch_andnot(int i, atomic_t *v)`             | 跟`~i`进行`逻辑与`                                                                         | 返回旧值                           |
| `void atomic_or(int i, atomic_t *v)`                      | 逻辑或                                                                                  | 无                              |
| `int atomic_fetch_or(int i, atomic_t *v)`                 | 逻辑或                                                                                  | 返回旧值                           |
| `void atomic_xor(int i, atomic_t *v)`                     | 异或                                                                                   | 无                              |
| `int atomic_fetch_xor(int i, atomic_t *v)`                | 异或                                                                                   | 返回旧值                           |
|                                                           |                                                                                      |                                |
| `test_and_set_bit`                                        | 检查并置1                                                                                | 如果已经置1，那么返回1。如果没有设置，那么进行置1，返回0 |
| `test_and_clear_bit`                                      | 检查并清0                                                                                | 如果已经清0，那么返回0。如果没有清0，那么进行清0，返回1 |
| `test_and_change_bit`                                     | 检查并改变                                                                                | 是1返回1，是0返回0，然后取反               |
## 实现说明
在5.x后面的版本中，atomic直接读写基本上都是调用READ_ONCE/WRITE_ONCE，基本上就是直接的内存读写，并没有额外逻辑控制。这两个宏仅保证读取操作的原子性和最新性，对于更复杂的并发控制，仍需要使用锁或者其他同步机制

```
#ifndef __READ_ONCE
#define __READ_ONCE(x)	(*(const volatile __unqual_scalar_typeof(x) *)&(x))
#endif

#define READ_ONCE(x)							\
({									\
	compiletime_assert_rwonce_type(x);				\
	__READ_ONCE(x);							\
})

#define __WRITE_ONCE(x, val)						\
do {									\
	*(volatile typeof(x) *)&(x) = (val);				\
} while (0)

#define WRITE_ONCE(x, val)						\
do {									\
	compiletime_assert_rwonce_type(x);				\
	__WRITE_ONCE(x, val);						\
} while (0)
```
所以atomic_read_acquire/atomic_set_release就是上面的两个接口加了smb屏障。
其他的一些操作，比如 atomic_add/atomic_fetch_add ，如果没有通过具体芯片定义，通用定义就是通过generic_atomic_add/generic_atomic_add_return来实现，举例来说其实就是加了关中断保护：
```
#define ATOMIC_FETCH_OP(op, c_op)                   \
static inline int generic_atomic_fetch_##op(int i, atomic_t *v)     \
{                                   \
    unsigned long flags;                        \
    int ret;                            \
                                   \
    raw_local_irq_save(flags);                  \
    ret = v->counter;                       \
    v->counter = v->counter c_op i;                 \
    raw_local_irq_restore(flags);                   \
                                    \
    return ret;                         \
}
```

而一般芯片有支持的，都是通过专门的汇编指令来实现的，以atomic_fetch_add为例，其实就是通过定义arch_atomic_fetch_add来实现，最终的实现在 /arch/arm64/include/asm/atomic_lse.h这里面，通过汇编操作 ldadd来实现：

```
static __always_inline int arch_##op##name(int i, atomic_t *v)      \
{                                   \
    return __lse_ll_sc_body(op##name, i, v);            \
}

#define __lse_ll_sc_body(op, ...)					\
({									\
	system_uses_lse_atomics() ?					\
		__lse_##op(__VA_ARGS__) :				\
		__ll_sc_##op(__VA_ARGS__);				\
})

#define ATOMIC_FETCH_OP(name, mb, op, asm_op, cl...)			\
static inline int __lse_atomic_fetch_##op##name(int i, atomic_t *v)	\
{									\
	asm volatile(							\
	__LSE_PREAMBLE							\
	"	" #asm_op #mb "	%w[i], %w[i], %[v]"			\
	: [i] "+r" (i), [v] "+Q" (v->counter)				\
	: "r" (v)							\
	: cl);								\
									\
	return i;							\
}
```
更详细的说明可以参考
ARM64-linux并发与同步之原子操作 
02-Linux-Kernel-armv8-aarch64-的原子操作的底层实现

