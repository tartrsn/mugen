import bisect
import copy
from fractions import Fraction
from functools import wraps
from itertools import groupby, zip_longest
from operator import attrgetter
from typing import List, Optional, Union

from mugen import lists
from mugen.constants import TIME_FORMAT
from mugen.lists import MugenList
from mugen.utilities import general, location
from mugen.utilities.conversion import (
    convert_float_to_fraction,
    convert_time_to_seconds,
)


class Event:
    """
    An event which occurs in some time sequence (i.e a song, or music video)
    """

    location: float
    duration: float

    @convert_time_to_seconds(["location", "duration"])
    def __init__(self, location: TIME_FORMAT = None, duration: float = 0):
        """
        Parameters
        ----------
        location
            location of the event in the time sequence (seconds)

        duration
            duration of the event (seconds)
        """
        self.location = location
        self.duration = duration

    def __lt__(self, other):
        return self.location < other.location

    def __repr__(self):
        return self.index_repr()

    def index_repr(self, index: Optional[int] = None):
        if index is None:
            repr_str = f"<{self.__class__.__name__}"
        else:
            repr_str = f"<{self.__class__.__name__} {index}"
        if self.location:
            repr_str += f", location: {self.location:.3f}"
        if self.duration:
            repr_str += f", duration:{self.duration:.3f}"
        repr_str += ">"

        return repr_str

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __ne__(self, other):
        return not self == other


def requires_end(func):
    """
    Decorator raises error if there is no end specified for the EventList
    """

    @wraps(func)
    def _requires_end(self, *args, **kwargs):
        if not self.end:
            raise ValueError(
                f"EventList's {func} method requires the 'end' attribute to be set."
            )

        return func(self, *args, **kwargs)

    return _requires_end


class EventList(MugenList):
    """
    A list of Events which occur in some time sequence
    """

    end: Optional[float]

    def __init__(
        self,
        events: Optional[List[Union[Event, TIME_FORMAT]]] = None,
        *,
        end: TIME_FORMAT = None,
    ):
        """
        Parameters
        ----------
        events
            events which occur in the time sequence

        end
            duration of the time sequence
        """
        if events is not None:
            for index, event in enumerate(events):
                if not isinstance(event, Event):
                    # Convert event location to Event
                    events[index] = Event(event)

        self.end = end

        super().__init__(events)

    def __eq__(self, other):
        return super().__eq__(other) and self.end == other.end

    def __add__(self, rhs):
        return type(self)((super().__add__(rhs)), end=rhs.end)

    def __getitem__(self, item):
        result = list.__getitem__(self, item)
        if isinstance(item, slice):
            return type(self)(result, end=self.end)
        else:
            return result

    def __repr__(self):
        event_reprs = [event.index_repr(index) for index, event in enumerate(self)]
        pretty_repr = super().pretty_repr(event_reprs)
        return f"<{pretty_repr}, end: {self.end}>"

    def list_repr(self, indexes: range, selected: bool):
        """
        Repr for use in lists
        """
        return (
            f"<{self.__class__.__name__} {indexes.start}-{indexes.stop} ({len(self)}), "
            f"type: {self.type}, selected: {selected}>"
        )

    @property
    def type(self) -> Union[str, None]:
        if len(self) == 0:
            return None
        elif len(set([event.__class__.__name__ for event in self])) == 1:
            return self[0].__class__.__name__
        else:
            return "mixed"

    @property
    def locations(self) -> List[float]:
        return [event.location for event in self]

    @property
    def intervals(self) -> List[float]:
        return location.intervals_from_locations(self.locations)

    @property
    def segment_locations(self):
        """
        Returns
        -------
        locations of segments between events
        """
        return [0] + [event.location for event in self]

    @property
    @requires_end
    def segment_durations(self):
        """
        Returns
        -------
        durations of segments between events
        """
        return location.intervals_from_locations(self.locations + [self.end])

    @property
    def durations(self) -> List[float]:
        return [event.duration for event in self]

    @property
    def types(self) -> List[str]:
        return [event.__class__.__name__ for event in self]

    def add_events(self, events: List[Union[Event, TIME_FORMAT]]):
        for event in events:
            if not isinstance(event, Event):
                event = Event(event)
            bisect.insort(self, event)

    def offset(self, offset: float):
        """
        Offsets all events by the given amount
        """
        for event in self:
            event.location += offset

    @convert_float_to_fraction("speed")
    def speed_multiply(
        self, speed: Union[float, Fraction], offset: Optional[int] = None
    ):
        """
        Speeds up or slows down events by grouping them together or splitting them up.
        For slowdowns, event type group boundaries and isolated events are preserved.

        Parameters
        ----------
        speed
            Factor to speedup or slowdown by.
            Must be of the form x (speedup) or 1/x (slowdown), where x is a natural number.
            Otherwise, 0 to remove all events.

        offset
            Offsets the grouping of events for slowdowns.
            Takes a max offset of x - 1 for a slowdown of 1/x, where x is a natural number
        """
        if speed == 0:
            self.clear()
        elif speed > 1:
            self._split(speed.numerator)
        elif speed < 1:
            self._merge(speed.denominator, offset)

    def _split(self, pieces_per_split: int):
        """
        Splits events up to form shorter intervals

        Parameters
        ----------
        pieces_per_split
            Number of pieces to split each event into
        """
        splintered_events = EventList()

        # Group events by type
        type_groups = self.group_by_type()

        for group in type_groups:
            if len(group) == 1:
                # Always keep isolated events
                splintered_events.append(group[0])
            else:
                for index, event in enumerate(group):
                    splintered_events.append(event)

                    if index == len(group) - 1:
                        # Skip last event
                        continue

                    next_event = group[index + 1]
                    interval = next_event.location - event.location
                    interval_piece = interval / pieces_per_split

                    location = event.location
                    for _ in range(pieces_per_split - 1):
                        location += interval_piece
                        event_splinter = copy.deepcopy(event)
                        event_splinter.location = location
                        splintered_events.append(event_splinter)

        self[:] = splintered_events

    def _merge(self, pieces_per_merge: int, offset: Optional[int] = None):
        """
        Merges adjacent events of identical type to form longer intervals

        Parameters
        ----------
        pieces_per_merge
            Number of adjacent events to merge at a time

        offset
            Offset for the merging of events
        """
        if offset is None:
            offset = 0

        combined_events = EventList()

        # Group events by type
        type_groups = self.group_by_type()
        for group in type_groups:
            if len(group) == 1:
                # Always keep isolated events
                combined_events.append(group[0])
            else:
                for index, event in enumerate(group):
                    if (index - offset) % pieces_per_merge == 0:
                        combined_events.append(event)

        self[:] = combined_events

    def group_by_type(self, select_types: List[str] = None) -> "EventGroupList":
        """
        Groups events by type

        Attributes
        ----------
        select_types
            A list of types for which to select groups in the resulting EventGroupList.
            If no types are specified, all resulting groups will be selected.

        Returns
        -------
        An EventGroupList partitioned by type
        """
        if select_types is None:
            select_types = []

        groups = [
            EventList(list(group), end=self.end)
            for index, group in groupby(self, key=attrgetter("__class__"))
        ]
        if not select_types:
            selected_groups = groups
        else:
            selected_groups = [group for group in groups if group.type in select_types]

        return EventGroupList(groups, selected=selected_groups)

    def group_by_slices(self, slices: (int, int)) -> "EventGroupList":
        """
        Groups events by slices.
        Does not support negative indexing.

        Slices explicitly passed in will become selected groups in the resulting EventGroupList.

        Returns
        -------
        An EventGroupList partitioned by slice
        """
        slices = [slice(sl[0], sl[1]) for sl in slices]

        # Fill in rest of slices
        all_slices = general.fill_slices(slices, len(self))
        target_indexes = [index for index, sl in enumerate(all_slices) if sl in slices]

        # Group events by slices
        groups = [self[sl] for sl in all_slices]
        selected_groups = [
            group for index, group in enumerate(groups) if index in target_indexes
        ]
        groups = EventGroupList(groups, selected=selected_groups)

        return groups


class EventGroupList(MugenList):
    """
    An alternate, more useful representation for a list of EventLists
    """

    _selected_groups: List[EventList]

    def __init__(
        self,
        groups: Optional[Union[List[EventList], List[List[TIME_FORMAT]]]] = None,
        *,
        selected: List[EventList] = None,
    ):
        """
        Parameters
        ----------
        groups

        selected
            A subset of groups to track
        """
        if groups is not None:
            for index, group in enumerate(groups):
                if not isinstance(group, EventList):
                    # Convert event locations to EventList
                    groups[index] = EventList(group)

        super().__init__(groups)

        self._selected_groups = selected if selected is not None else []

    def __repr__(self):
        group_reprs = []
        index_count = 0
        for group in self:
            group_indexes = range(index_count, index_count + len(group) - 1)
            group_reprs.append(
                group.list_repr(
                    group_indexes, True if group in self.selected_groups else False
                )
            )
            index_count += len(group)
        return super().pretty_repr(group_reprs)

    @property
    def end(self):
        return self[-1].end if self else None

    @property
    def selected_groups(self) -> "EventGroupList":
        """
        Returns
        -------
        Selected groups
        """
        return EventGroupList([group for group in self._selected_groups])

    @property
    def unselected_groups(self) -> "EventGroupList":
        """
        Returns
        -------
        Unselected groups
        """
        return EventGroupList(
            [group for group in self if group not in self.selected_groups]
        )

    def speed_multiply(
        self, speeds: List[float], offsets: Optional[List[float]] = None
    ):
        """
        Speed multiplies event groups, in order

        See :meth:`~mugen.events.EventList.speed_multiply` for further information.
        """
        if offsets is None:
            offsets = []

        for group, speed, offset in zip_longest(self, speeds, offsets):
            group.speed_multiply(speed if speed is not None else 1, offset)

    def flatten(self) -> EventList:
        """
        Flattens the EventGroupList back into an EventList.

        Returns
        -------
        A flattened EventList for this EventGroupList
        """
        return EventList(lists.flatten(self), end=self.end)
