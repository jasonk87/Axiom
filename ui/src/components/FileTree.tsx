import { useEffect, useMemo, useState } from "react";
import type { FileTreeNode } from "../types";

type FileTreeProps = {
  nodes: FileTreeNode[];
  onSelect: (path: string) => void;
  selectedPath?: string;
};

function FileTreeNodeView({
  node,
  onSelect,
  selectedPath,
  expandedPaths,
  toggleExpanded,
}: {
  node: FileTreeNode;
  onSelect: (path: string) => void;
  selectedPath?: string;
  expandedPaths: Set<string>;
  toggleExpanded: (path: string) => void;
}) {
  const isDirectory = node.type === "directory";
  const isExpanded = isDirectory ? expandedPaths.has(node.path) : false;
  const isSelected = selectedPath === node.path;

  return (
    <li className="tree-node">
      <button
        className={isSelected ? "tree-node-button active" : "tree-node-button"}
        onClick={() => {
          if (isDirectory) {
            toggleExpanded(node.path);
          }
          onSelect(node.path);
        }}
      >
        <span className="tree-node-caret">{isDirectory ? (isExpanded ? "▾" : "▸") : "•"}</span>
        <span className={`tree-node-type tree-node-type-${node.type}`}>{node.type === "directory" ? "DIR" : "FILE"}</span>
        <span>{node.name}</span>
      </button>
      {node.children && node.children.length > 0 && isExpanded ? (
        <ul className="tree-children">
          {node.children.map((child) => (
            <FileTreeNodeView
              key={child.path}
              node={child}
              onSelect={onSelect}
              selectedPath={selectedPath}
              expandedPaths={expandedPaths}
              toggleExpanded={toggleExpanded}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export function FileTree({ nodes, onSelect, selectedPath }: FileTreeProps) {
  const defaultExpanded = useMemo(() => {
    const values = new Set<string>();
    nodes.forEach((node) => {
      if (node.type === "directory") {
        values.add(node.path);
      }
    });
    return values;
  }, [nodes]);
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(defaultExpanded);

  useEffect(() => {
    setExpandedPaths(defaultExpanded);
  }, [defaultExpanded]);

  function toggleExpanded(path: string) {
    setExpandedPaths((current) => {
      const next = new Set(current);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }

  return (
    <ul className="tree-root">
      {nodes.map((node) => (
        <FileTreeNodeView
          key={node.path}
          node={node}
          onSelect={onSelect}
          selectedPath={selectedPath}
          expandedPaths={expandedPaths}
          toggleExpanded={toggleExpanded}
        />
      ))}
    </ul>
  );
}
