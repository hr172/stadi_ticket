def find_adjacent_seats(available_seats, group_size):
    """
    Find the first run of `group_size` consecutive seat numbers.
    Returns the list of seat numbers, or None if no such run exists.
    """
    if not available_seats or len(available_seats) < group_size:
        return None

    available_seats = sorted(available_seats)

    for i in range(len(available_seats) - group_size + 1):
        block = available_seats[i:i + group_size]
        if block[-1] - block[0] == group_size - 1:
            return block

    return None


def find_adjacent_clusters_for_map(available_seats, group_size):
    """
    Find all non-overlapping runs of `group_size` consecutive seat numbers.
    Returns a list of cluster dicts with keys: seats, start, end, count.
    Used to populate the group interactive map with highlighted clusters.
    """
    if not available_seats or len(available_seats) < group_size:
        return []

    available_seats = sorted(available_seats)
    clusters = []
    i = 0

    while i <= len(available_seats) - group_size:
        block = available_seats[i:i + group_size]
        if block[-1] - block[0] == group_size - 1:
            clusters.append({
                'seats': block,
                'start': block[0],
                'end': block[-1],
                'count': len(block)
            })
            i += group_size   # skip past this cluster to avoid overlaps
        else:
            i += 1

    return clusters


def find_best_adjacent_seats(available_seats, group_size, preference='center'):
    """
    Find the best cluster of `group_size` consecutive seats based on a preference.
    preference: 'center' — closest to the middle of available inventory
                'aisle'  — closest to the edges (low or high seat numbers)
    Returns the winning cluster dict, or None.
    """
    clusters = find_adjacent_clusters_for_map(available_seats, group_size)
    if not clusters:
        return None

    if preference == 'center':
        middle = (min(available_seats) + max(available_seats)) / 2
        return min(clusters, key=lambda c: abs((c['start'] + c['end']) / 2 - middle))
    elif preference == 'aisle':
        edge = min(available_seats)
        edge_clusters = [c for c in clusters if c['start'] <= edge + 10]
        return edge_clusters[0] if edge_clusters else clusters[0]

    return clusters[0]
