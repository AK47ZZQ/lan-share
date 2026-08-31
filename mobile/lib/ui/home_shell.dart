// 主壳：底部导航（照片/文件/我的），M-A1 信息架构的最小落地。
import 'package:flutter/material.dart';

import 'files_page.dart';
import 'me_page.dart';
import 'photos_page.dart';

class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _tab = 0;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: IndexedStack(index: _tab, children: [
        PhotosPage(onGoFiles: () => setState(() => _tab = 1)),
        const FilesPage(),
        MePage(onGoFiles: () => setState(() => _tab = 1)),
      ]),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: (i) => setState(() => _tab = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.photo_outlined),
              selectedIcon: Icon(Icons.photo), label: '照片'),
          NavigationDestination(icon: Icon(Icons.folder_outlined),
              selectedIcon: Icon(Icons.folder), label: '文件'),
          NavigationDestination(icon: Icon(Icons.person_outline),
              selectedIcon: Icon(Icons.person), label: '我的'),
        ],
      ),
    );
  }
}
