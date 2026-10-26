#pragma once
// Linux: the number of this process's threads that are alive and not exiting.
//
// pthread_join (and std::thread::join) returns when the kernel clears the thread's TID in
// mm_release, early in do_exit; the thread leaves /proc/self/task only later, when
// exit_notify releases it. In between, a joined thread is still listed, with PF_EXITING
// (0x4, set at the very start of do_exit) in field 9 of its stat line. Counting directory
// entries therefore overcounts right after a join, for longer the busier the CPU is (on one
// CPU about half of all joins were still listed when counted at once). Leaving out exiting
// threads counts exactly the threads that have not been joined or have not ended; a leaked
// or still-running thread is never exiting, so it is always counted.
#ifdef __linux__
#include <dirent.h>
#include <fcntl.h>
#include <unistd.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace dtl_test {
inline long live_threads() {
    DIR *dir = opendir("/proc/self/task");
    if (!dir) return -1;
    long n = 0;
    while (const dirent *entry = readdir(dir)) {
        if (entry->d_name[0] == '.') continue;
        char path[sizeof "/proc/self/task//stat" + sizeof entry->d_name], stat[1024];
        std::snprintf(path, sizeof path, "/proc/self/task/%s/stat", entry->d_name);
        const int fd = open(path, O_RDONLY | O_CLOEXEC);
        if (fd < 0) continue; // released since readdir listed it
        const ssize_t got = read(fd, stat, sizeof stat - 1);
        close(fd);
        if (got <= 0) continue;
        stat[got] = '\0';
        // "pid (comm) state ppid pgrp session tty_nr tpgid flags ...": comm may hold spaces and
        // parentheses, so the fields start after the last ')'.
        const char *field = std::strrchr(stat, ')');
        if (!field) continue;
        for (int skip = 0; skip < 7 && field; ++skip) field = std::strchr(field + 1, ' ');
        if (!field) continue;
        const unsigned long flags = std::strtoul(field + 1, nullptr, 10);
        if (!(flags & 0x4)) ++n; // PF_EXITING
    }
    closedir(dir);
    return n;
}
}
#else
namespace dtl_test {
// No /proc/self/task: the census is unavailable (callers that need it are Linux-only).
inline long live_threads() { return -1; }
}
#endif
