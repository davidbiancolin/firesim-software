// See LICENSE for license details.

//**************************************************************************
// Quicksort benchmark
//--------------------------------------------------------------------------
//
// This benchmark uses quicksort to sort an array of integers. The
// implementation is largely adapted from Numerical Recipes for C. The
// input data (and reference data) should be generated using the
// qsort_gendata.pl perl script and dumped to a file named
// dataset1.h The smips-gcc toolchain does not support system calls
// so printf's can only be used on a host system, not on the smips
// processor simulator itself. You should not change anything except
// the HOST_DEBUG and PREALLOCATE macros for your timing run.

#include "util.h"
#include <string.h>
#include <assert.h>
#include <stdlib.h>
#include <stdio.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <stdbool.h>

// The INSERTION_THRESHOLD is the size of the subarray when the
// algorithm switches to using an insertion sort instead of
// quick sort.

#define INSERTION_THRESHOLD 10

// NSTACK is the required auxiliary storage.
// It must be at least 2*lg(DATA_SIZE)

#define NSTACK 50

//--------------------------------------------------------------------------
// Input/Reference Data

#define type int32_t

// Swap macro for swapping two values.

#define SWAP(a,b) do { typeof(a) temp=(a);(a)=(b);(b)=temp; } while (0)
#define SWAP_IF_GREATER(a, b) do { if ((a) > (b)) SWAP(a, b); } while (0)

/* A global counter for progress updates */
int64_t ins_count = 0;
int64_t print_count = 0;

//--------------------------------------------------------------------------
// Quicksort function

static void insertion_sort(size_t n, type arr[])
{
  type *i, *j;
  type value;
  if((++ins_count % 65536) == 0) {
    print_count++;
    if((print_count % 20) == 0) {
      printf("\33[2K\r");
    }
    putchar('.');
    fflush(stdout);
  }
  for (i = arr+1; i < arr+n; i++)
  {
    value = *i;
    j = i;
    while (value < *(j-1))
    {
      *j = *(j-1);
      if (--j == arr)
        break;
    }
    *j = value;
  }
}

static void selection_sort(size_t n, type arr[])
{
  for (type* i = arr; i < arr+n-1; i++)
    for (type* j = i+1; j < arr+n; j++)
      SWAP_IF_GREATER(*i, *j);
}

void sort(size_t n, type arr[])
{
  type* ir = arr+n;
  type* l = arr+1;
  type* stack[NSTACK];
  type** stackp = stack;

  printf("\n");
  for (;;)
  {
#if HOST_DEBUG
    printArray( "", n, arr );
#endif

    // Insertion sort when subarray small enough.
    if ( ir-l < INSERTION_THRESHOLD )
    {
      insertion_sort(ir - l + 1, l - 1);

      if ( stackp == stack ) break;

      // Pop stack and begin a new round of partitioning.
      ir = *stackp--;
      l = *stackp--;
    }
    else
    {
      // Choose median of left, center, and right elements as
      // partitioning element a. Also rearrange so that a[l-1] <= a[l] <= a[ir-].
      SWAP(arr[((l-arr) + (ir-arr))/2-1], l[0]);
      SWAP_IF_GREATER(l[-1], ir[-1]);
      SWAP_IF_GREATER(l[0], ir[-1]);
      SWAP_IF_GREATER(l[-1], l[0]);

      // Initialize pointers for partitioning.
      type* i = l+1;
      type* j = ir;

      // Partitioning element.
      type a = l[0];

      for (;;) {                    // Beginning of innermost loop.
        while (*i++ < a);           // Scan up to find element > a.
        while (*(j-- - 2) > a);     // Scan down to find element < a.
        if (j < i) break;           // Pointers crossed. Partitioning complete.
        SWAP(i[-1], j[-1]);         // Exchange elements.
      }                             // End of innermost loop.

      // Insert partitioning element.
      l[0] = j[-1];
      j[-1] = a;
      stackp += 2;

      // Push pointers to larger subarray on stack,
      // process smaller subarray immediately.

#if HOST_DEBUG
      assert(stackp < stack+NSTACK);
#endif

      if ( ir-i+1 >= j-l )
      {
        stackp[0] = ir;
        stackp[-1] = i;
        ir = j-1;
      }
      else
      {
        stackp[0] = j-1;
        stackp[-1] = l;
        l = i;
      }
    }
  }
  printf("\n");
}

//--------------------------------------------------------------------------
// Main

#ifdef RISCV
uint64_t get_cycle(void)
{
  register unsigned long __v;
  __asm__ __volatile__ ("rdcycle %0" : "=r" (__v));
  return __v;
}
#else
uint64_t get_cycle(void)
{
  return 0;
}
#endif

bool check_sort(type *arr, size_t n)
{ 
  for(int i = 0; i < (n - 1); i++) {
    if(arr[i] > arr[i+1]) {
      return false;
    }
  }

  return true;
}

int main( int argc, char* argv[] )
{
  uint64_t start, end;

  start = get_cycle();

  if(argc != 2) {
    printf("usage: ./qsort SIZE\n\tSIZE - size of array to sort (in bytes)\n");
    return EXIT_FAILURE;
  }
  
  size_t sz = atol(argv[1]);
  size_t n = sz / sizeof(type);
  type *arr = malloc(sz);

  srand(0);
  for(int i = 0; i < n; i++) {
    arr[i] = rand();
  }

  printf("Gonna sort me sum datas!\n");
  // Do the sort
  sort(n, arr);
  end = get_cycle();
  printf("Took %ld Cycles\n", end - start);
  if(check_sort(arr, n)) {
    printf("Prolly sorted 'em by now\n");
  } else {
    printf("I sorted wrong!!!!\n");
    return EXIT_FAILURE;
  }

  return EXIT_SUCCESS;
}
