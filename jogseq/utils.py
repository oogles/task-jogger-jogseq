import datetime
import os
import re

from .exceptions import ParseError

TASK_ID_RE = re.compile(r'^([A-Z]+-\d+):?$')


def parse_journal(graph_path, date):
    
    journal_path = os.path.join(graph_path, 'journals', f'{date:%Y_%m_%d}.md')
    
    journal = Journal(date)
    current_block = journal
    
    with open(journal_path, 'r') as f:
        for line in f.readlines():
            indent = line.count('\t')
            content = line.strip()
            
            if not content.startswith('-'):
                # The line is a continuation of the current block
                current_block.add_line(content)
                continue
            
            block_cls = Block
            if content.startswith('- NOW ') or content.startswith('- LATER '):
                block_cls = Task
            
            if indent > current_block.indent:
                # The line is a child block of the current block
                parent_block = current_block
            elif indent == current_block.indent:
                # The line is a sibling block of the current block
                parent_block = current_block.parent
            else:
                # The line is a new block at a higher level than the
                # current block. Step back through the current block's
                # parents to the appropriate level and add a new child
                # block there.
                while indent <= current_block.indent:
                    current_block = current_block.parent
                
                parent_block = current_block
            
            current_block = block_cls(indent, content, parent_block)
            
            if '[CATCH-ALL]' in current_block.content:
                journal.catch_all_block = current_block
    
    return journal


def parse_duration_timestamp(timestamp_str):
    
    # Extract hours, minutes, and seconds from the string in H:M:S format,
    # and cast as integers
    hours, minutes, seconds = map(int, timestamp_str.split(':'))
    
    # Convert the duration into seconds
    return hours * 3600 + minutes * 60 + seconds


def parse_duration_input(input_str):
    
    # Extract hours and minutes from the string in "Xh Ym" format, and cast
    # as integers
    parts = input_str.split()
    hours, minutes = 0, 0
    for part in parts:
        if part.endswith('h'):
            hours = int(part[:-1])
        elif part.endswith('m'):
            minutes += int(part[:-1])
        else:
            raise ParseError('Invalid duration string format. Only hours and minutes are supported.')
    
    # Convert the duration into seconds
    return hours * 3600 + minutes * 60


def round_duration(total_seconds):
    
    interval = 60 * 5  # 5 minutes
    
    # If a zero duration, report it as such. But for other durations less
    # than the interval, report the interval as a minimum instead.
    if not total_seconds:
        return 0
    elif total_seconds < interval:
        return interval
    
    # Round to the most appropriate 5-minute interval
    base, remainder = divmod(total_seconds, interval)
    
    duration = interval * base
    
    # If more than 90 seconds into the next interval, round up
    if remainder > 90:
        duration += interval
    
    return duration


def format_duration(total_seconds):
    
    # Calculate hours, minutes, and seconds
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    # Create the formatted duration string
    parts = []
    if hours > 0:
        parts.append(f'{hours}h')
    if minutes > 0:
        parts.append(f'{minutes}m')
    if seconds > 0 or not parts:
        parts.append(f'{seconds}s')
    
    return ' '.join(parts)


def find_tasks(block):
    
    tasks = []
    for child in block.children:
        if isinstance(child, Task):
            tasks.append(child)
        
        tasks.extend(find_tasks(child))
    
    return tasks


class LogbookEntry:
    
    @classmethod
    def from_duration(cls, date, duration):
        
        # Fudge some timestamps and format a compatible logbook entry based
        # on the duration
        start_time = datetime.datetime(date.year, month=date.month, day=date.day, hour=0, minute=0)
        end_time = start_time + datetime.timedelta(seconds=duration)
        
        date_format = '%Y-%m-%d %a %H:%M:%S'
        start_time_str = start_time.strftime(date_format)
        end_time_str = end_time.strftime(date_format)
        
        hours, remainder = divmod(duration, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        return cls(f'CLOCK: [{start_time_str}]--[{end_time_str}] => {hours:02}:{minutes:02}:{seconds:02}')
    
    def __init__(self, content):
        
        self.content = content
        self._duration = None
    
    @property
    def duration(self):
        
        if self._duration is None:
            duration_str = self.content.split('=>')[1].strip()
            self._duration = parse_duration_timestamp(duration_str)
        
        return self._duration


class Block:
    
    def __init__(self, indent, content, parent=None):
        
        self.indent = indent
        self.parent = parent
        
        self.content = content.replace('-', '', 1).strip()
        
        self.properties = {}
        self.extra_lines = []
        self.children = []
        
        if parent:
            parent.children.append(self)
    
    def _process_new_line(self, content):
        
        if content and content.split()[0].endswith('::'):
            # The line is a property of the block
            key, value = content.split('::', 1)
            
            if key in self.properties:
                raise ParseError(f'Duplicate property "{key}" for block "{self.content}".')
            
            self.properties[key] = value.strip()
            return None
        
        return content
    
    def add_line(self, content):
        
        content = content.strip()
        
        content = self._process_new_line(content)
        
        if content is not None:  # allow blank lines, just not explicitly nullified lines
            self.extra_lines.append(content)


class Task(Block):
    
    def __init__(self, *args, **kwargs):
        
        super().__init__(*args, **kwargs)
        
        # Split content into keyword (e.g. LATER), task ID, and any optional
        # remaining content
        keyword, *remainder = self.content.split(' ', 2)
        
        # At least one item in the remainder should always exist, because
        # Tasks are only created if a matching keyword *followed by a space*
        # is found at the start of the line's content
        task_id = remainder[0]
        if TASK_ID_RE.match(task_id):
            # Remove the task ID from the remainder - the rest (if any) will be
            # the task description
            task_id = task_id.strip(':')
            description = remainder[1:]
        else:
            # The first item of the remainder does not appear to be a task ID,
            # consider it part of the description
            task_id = None
            description = remainder
        
        self.keyword = keyword
        self.task_id = task_id
        self.description = ' '.join(description)
        
        self.logbook = []
    
    def _process_new_line(self, content):
        
        content = super()._process_new_line(content)
        
        # Ignore logbook start/end entries
        if content in (':LOGBOOK:', ':END:'):
            return None
        elif content and content.startswith('CLOCK:'):
            # Logbook timers started and stopped in the same second do
            # not record a duration. They don't need to be processed or
            # reproduced, they can be ignored.
            if '=>' in content:
                self.logbook.append(LogbookEntry(content))
            
            return None
        
        return content
    
    def add_to_logbook(self, date, duration):
        
        entry = LogbookEntry.from_duration(date, duration)
        
        self.logbook.insert(0, entry)
    
    def validate(self):
        
        errors = {}
        
        def add_error(error_type, error):
            
            errors.setdefault(error_type, [])
            errors[error_type].append(error)
        
        # Ensure the task's timer isn't currently running
        if self.keyword == 'NOW':
            add_error('keyword', 'Running timer detected')
        
        # Ensure the task is not a child of another task
        p = self.parent
        while p:
            if isinstance(p, Task):
                add_error('keyword', 'Nested task detected')
                break
            
            p = p.parent
        
        # Ensure the task has an ID and a duration
        if not self.task_id:
            add_error('task_id', 'No task ID')
        
        if not self.logbook:
            add_error('duration', 'No duration recorded')
        
        # If a type:: property remains, it's because it's in an invalid format
        if 'time' in self.properties:
            add_error('duration', 'Invalid format for "time" property')
        
        return errors
    
    def get_total_duration(self):
        
        total = sum(log.duration for log in self.logbook)
        
        return round_duration(total)


class Journal(Block):
    
    def __init__(self, date):
        
        super().__init__(indent=-1, content='', parent=None)
        
        self.date = date
        
        self._catch_all_block = None
        self._tasks = None
    
    @property
    def catch_all_block(self):
        
        return self._catch_all_block
    
    @catch_all_block.setter
    def catch_all_block(self, block):
        
        if self._catch_all_block and self._catch_all_block is not block:
            # The journal already has a catch-all task registered, and it is
            # different to the one given
            raise ParseError('Only a single CATCH-ALL block is supported per journal.')
        
        self._catch_all_block = block
    
    @property
    def tasks(self):
        
        if self._tasks is None:
            raise Exception('Tasks not collated. Call process_tasks() first.')
        
        return self._tasks
    
    def process_tasks(self, switching_cost):
        
        log = []
        date = self.date
        
        all_tasks = self._tasks = find_tasks(self)
        num_tasks = len(all_tasks)
        
        # Calculate and log context switching cost (in seconds)
        total_switching_cost = round_duration((num_tasks * switching_cost) * 60)
        catch_all_block = self.catch_all_block
        if catch_all_block:
            catch_all_block.add_to_logbook(date, total_switching_cost)
        elif total_switching_cost > 0:
            log.append(('warning', (
                'No CATCH-ALL task found to log context switching cost against. '
                'Not included in total duration.'
            )))
        
        # Check tasks for a time:: property and convert it to a logbook entry
        # if found
        for task in all_tasks:
            if 'time' in task.properties:
                time_value = task.properties['time']
                
                # If the value isn't a valid duration string, leave the
                # property in place as a flag that the task isn't valid to
                # be logged. Otherwise remove it and replace it with a
                # logbook entry.
                try:
                    time_value = parse_duration_input(time_value)
                except ParseError:
                    pass
                else:
                    del task.properties['time']
                    
                    # Manually-entered times are likely to be rounded already,
                    # but just in case...
                    time_value = round_duration(time_value)
                    
                    task.add_to_logbook(date, time_value)
            
            errors = task.validate()
            for messages in errors.values():
                for msg in messages:
                    log.append(('error', f'{msg} for line "{task.content}"'))
        
        # Calculate the total duration
        total_duration = sum(t.get_total_duration() for t in all_tasks)
        
        return {
            'tasks': all_tasks,
            'total_duration': total_duration,
            'total_switching_cost': total_switching_cost,
            'log': log
        }
