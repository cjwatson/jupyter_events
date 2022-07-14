"""
Emit structured, discrete events when various actions happen.
"""
import json
import logging
from datetime import datetime

from pythonjsonlogger import jsonlogger
from traitlets import Instance, List, default
from traitlets.config import Config, Configurable

from . import EVENTS_METADATA_VERSION
from .schema_registry import SchemaRegistry
from .traits import Handlers


class EventLogger(Configurable):
    """
    Send structured events to a logging sink
    """

    handlers = Handlers(
        default_value=[],
        allow_none=True,
        help="""A list of logging.Handler instances to send events to.

        When set to None (the default), events are discarded.
        """,
    ).tag(config=True)

    allowed_policies = List(
        default_value=["all"],
        help=(
            """
            A list of the redaction policies that will not be redacted
            from incoming, recorded events.
            """
        ),
    )

    schema_registry = Instance(SchemaRegistry)

    @default("schema_registry")
    def _default_schema_registry(self):
        return SchemaRegistry(allowed_policies=self.allowed_policies)

    def __init__(self, *args, **kwargs):
        # We need to initialize the configurable before
        # adding the logging handlers.
        super().__init__(*args, **kwargs)
        # Use a unique name for the logger so that multiple instances of EventLog do not write
        # to each other's handlers.
        log_name = __name__ + "." + str(id(self))
        self.log = logging.getLogger(log_name)
        # We don't want events to show up in the default logs
        self.log.propagate = False
        # We will use log.info to emit
        self.log.setLevel(logging.INFO)
        # Add each handler to the logger and format the handlers.
        if self.handlers:
            for handler in self.handlers:
                self.add_handler(handler)

    def _load_config(self, cfg, section_names=None, traits=None):
        """Load EventLogger traits from a Config object, patching the
        handlers trait in the Config object to avoid deepcopy errors.
        """
        my_cfg = self._find_my_config(cfg)
        handlers = my_cfg.pop("handlers", [])

        # Turn handlers list into a pickeable function
        def get_handlers():
            return handlers

        my_cfg["handlers"] = get_handlers

        # Build a new eventlog config object.
        eventlogger_cfg = Config({"EventLogger": my_cfg})
        super()._load_config(eventlogger_cfg, section_names=None, traits=None)

    def register_schema(self, schema: dict):
        """Register events schema with the SchemaRegistry."""
        self.schema_registry.register(schema)

    def register_schema_file(self, schema_filepath):
        """Register events schema with the SchemaRegistry."""
        self.schema_registry.register_from_file(schema_filepath)

    def add_handler(self, handler: logging.Handler):
        """Add a new logging handler to the Event Logger.

        All outgoing messages will be formatted as a JSON string.
        """

        def _skip_message(record, **kwargs):
            """
            Remove 'message' from log record.
            It is always emitted with 'null', and we do not want it,
            since we are always emitting events only
            """
            del record["message"]
            return json.dumps(record, **kwargs)

        formatter = jsonlogger.JsonFormatter(json_serializer=_skip_message)
        handler.setFormatter(formatter)
        self.log.addHandler(handler)
        if handler not in self.handlers:
            self.handlers.append(handler)

    def remove_handler(self, handler):
        """Remove the logging handler from the logger and list of handlers."""
        self.log.removeHandler(handler)
        if handler in self.handlers:
            self.handlers.remove(handler)

    def emit(self, schema_name, version, event, timestamp_override=None):
        """
        Record given event with schema has occurred.

        Parameters
        ----------
        schema_name: str
            Name of the schema
        version: str
            The schema version
        event: dict
            The event to record
        timestamp_override: datetime, optional
            Optionally override the event timestamp. By default it is set to the current timestamp.

        Returns
        -------
        dict
            The recorded event data
        """
        if not self.handlers or (schema_name, version) not in self.schema_registry:
            # if handler isn't set up or schema is not explicitly whitelisted,
            # don't do anything
            return

        # Generate the empty event capsule.
        if timestamp_override is None:
            timestamp = datetime.utcnow()
        else:
            timestamp = timestamp_override
        capsule = {
            "__timestamp__": timestamp.isoformat() + "Z",
            "__schema__": schema_name,
            "__schema_version__": version,
            "__metadata_version__": EVENTS_METADATA_VERSION,
        }
        schema = self.schema_registry.get((schema_name, version))
        schema.validate(event)
        schema.enforce_redaction_policies(event)
        capsule.update(event)
        self.log.info(capsule)
        return capsule
