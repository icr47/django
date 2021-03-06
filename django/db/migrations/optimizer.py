from django.db import migrations


class MigrationOptimizer(object):
    """
    Powers the optimization process, where you provide a list of Operations
    and you are returned a list of equal or shorter length - operations
    are merged into one if possible.

    For example, a CreateModel and an AddField can be optimised into a
    new CreateModel, and CreateModel and DeleteModel can be optimised into
    nothing.
    """

    def optimize(self, operations, app_label=None):
        """
        Main optimization entry point. Pass in a list of Operation instances,
        get out a new list of Operation instances.

        Unfortunately, due to the scope of the optimisation (two combinable
        operations might be separated by several hundred others), this can't be
        done as a peephole optimisation with checks/output implemented on
        the Operations themselves; instead, the optimizer looks at each
        individual operation and scans forwards in the list to see if there
        are any matches, stopping at boundaries - operations which can't
        be optimized over (RunSQL, operations on the same field/model, etc.)

        The inner loop is run until the starting list is the same as the result
        list, and then the result is returned. This means that operation
        optimization must be stable and always return an equal or shorter list.

        The app_label argument is optional, but if you pass it you'll get more
        efficient optimization.
        """
        # Internal tracking variable for test assertions about # of loops
        self._iterations = 0
        while True:
            result = self.optimize_inner(operations, app_label)
            self._iterations += 1
            if result == operations:
                return result
            operations = result

    def optimize_inner(self, operations, app_label=None):
        """
        Inner optimization loop.
        """
        new_operations = []
        for i, operation in enumerate(operations):
            # Compare it to each operation after it
            for j, other in enumerate(operations[i + 1:]):
                result = self.reduce(operation, other)
                if result is not None:
                    # Optimize! Add result, then remaining others, then return
                    new_operations.extend(result)
                    new_operations.extend(operations[i + 1:i + 1 + j])
                    new_operations.extend(operations[i + j + 2:])
                    return new_operations
                if not self.can_optimize_through(operation, other, app_label):
                    new_operations.append(operation)
                    break
            else:
                new_operations.append(operation)
        return new_operations

    #### REDUCTION ####

    def reduce(self, operation, other):
        """
        Either returns a list of zero, one or two operations,
        or None, meaning this pair cannot be optimized.
        """
        submethods = [
            (
                migrations.CreateModel,
                migrations.DeleteModel,
                self.reduce_model_create_delete,
            ),
            (
                migrations.AlterModelTable,
                migrations.DeleteModel,
                self.reduce_model_alter_delete,
            ),
            (
                migrations.AlterUniqueTogether,
                migrations.DeleteModel,
                self.reduce_model_alter_delete,
            ),
            (
                migrations.AlterIndexTogether,
                migrations.DeleteModel,
                self.reduce_model_alter_delete,
            ),
            (
                migrations.CreateModel,
                migrations.RenameModel,
                self.reduce_model_create_rename,
            ),
            (
                migrations.RenameModel,
                migrations.RenameModel,
                self.reduce_model_rename_self,
            ),
            (
                migrations.CreateModel,
                migrations.AddField,
                self.reduce_create_model_add_field,
            ),
            (
                migrations.CreateModel,
                migrations.AlterField,
                self.reduce_create_model_alter_field,
            ),
            (
                migrations.CreateModel,
                migrations.RemoveField,
                self.reduce_create_model_remove_field,
            ),
            (
                migrations.AddField,
                migrations.AlterField,
                self.reduce_add_field_alter_field,
            ),
            (
                migrations.AddField,
                migrations.RemoveField,
                self.reduce_add_field_delete_field,
            ),
            (
                migrations.AlterField,
                migrations.RemoveField,
                self.reduce_alter_field_delete_field,
            ),
            (
                migrations.AddField,
                migrations.RenameField,
                self.reduce_add_field_rename_field,
            ),
            (
                migrations.AlterField,
                migrations.RenameField,
                self.reduce_alter_field_rename_field,
            ),
            (
                migrations.CreateModel,
                migrations.RenameField,
                self.reduce_create_model_rename_field,
            ),
            (
                migrations.RenameField,
                migrations.RenameField,
                self.reduce_rename_field_self,
            ),
        ]
        for ia, ib, om in submethods:
            if isinstance(operation, ia) and isinstance(other, ib):
                return om(operation, other)
        return None

    def reduce_model_create_delete(self, operation, other):
        """
        Folds a CreateModel and a DeleteModel into nothing.
        """
        if operation.name.lower() == other.name.lower():
            return []

    def reduce_model_alter_delete(self, operation, other):
        """
        Folds an AlterModelSomething and a DeleteModel into just delete.
        """
        if operation.name.lower() == other.name.lower():
            return [other]

    def reduce_model_create_rename(self, operation, other):
        """
        Folds a model rename into its create
        """
        if operation.name.lower() == other.old_name.lower():
            return [migrations.CreateModel(
                other.new_name,
                fields=operation.fields,
                options=operation.options,
                bases=operation.bases,
            )]

    def reduce_model_rename_self(self, operation, other):
        """
        Folds a model rename into another one
        """
        if operation.new_name.lower() == other.old_name.lower():
            return [
                migrations.RenameModel(
                    operation.old_name,
                    other.new_name,
                )
            ]

    def reduce_create_model_add_field(self, operation, other):
        if operation.name.lower() == other.model_name.lower():
            return [migrations.CreateModel(
                operation.name,
                fields=operation.fields + [(other.name, other.field)],
                options=operation.options,
                bases=operation.bases,
            )]

    def reduce_create_model_alter_field(self, operation, other):
        if operation.name.lower() == other.model_name.lower():
            return [migrations.CreateModel(
                operation.name,
                fields=[
                    (n, other.field if n == other.name else v)
                    for n, v in operation.fields
                ],
                options=operation.options,
                bases=operation.bases,
            )]

    def reduce_create_model_rename_field(self, operation, other):
        if operation.name.lower() == other.model_name.lower():
            return [migrations.CreateModel(
                operation.name,
                fields=[
                    (other.new_name if n == other.old_name else n, v)
                    for n, v in operation.fields
                ],
                options=operation.options,
                bases=operation.bases,
            )]

    def reduce_create_model_remove_field(self, operation, other):
        if operation.name.lower() == other.model_name.lower():
            return [migrations.CreateModel(
                operation.name,
                fields=[
                    (n, v)
                    for n, v in operation.fields
                    if n.lower() != other.name.lower()
                ],
                options=operation.options,
                bases=operation.bases,
            )]

    def reduce_add_field_alter_field(self, operation, other):
        if operation.model_name.lower() == other.model_name.lower() and operation.name.lower() == other.name.lower():
            return [migrations.AddField(
                model_name=operation.model_name,
                name=operation.name,
                field=other.field,
            )]

    def reduce_add_field_delete_field(self, operation, other):
        if operation.model_name.lower() == other.model_name.lower() and operation.name.lower() == other.name.lower():
            return []

    def reduce_alter_field_delete_field(self, operation, other):
        if operation.model_name.lower() == other.model_name.lower() and operation.name.lower() == other.name.lower():
            return [other]

    def reduce_add_field_rename_field(self, operation, other):
        if operation.model_name.lower() == other.model_name.lower() and operation.name.lower() == other.old_name.lower():
            return [migrations.AddField(
                model_name=operation.model_name,
                name=other.new_name,
                field=operation.field,
            )]

    def reduce_alter_field_rename_field(self, operation, other):
        if operation.model_name.lower() == other.model_name.lower() and operation.name.lower() == other.old_name.lower():
            return [
                other,
                migrations.AlterField(
                    model_name=operation.model_name,
                    name=other.new_name,
                    field=operation.field,
                ),
            ]

    def reduce_rename_field_self(self, operation, other):
        if operation.model_name.lower() == other.model_name.lower() and operation.new_name.lower() == other.old_name.lower():
            return [
                migrations.RenameField(
                    operation.model_name,
                    operation.old_name,
                    other.new_name,
                ),
            ]

    #### THROUGH CHECKS ####

    def can_optimize_through(self, operation, other, app_label=None):
        """
        Returns True if it's possible to optimize 'operation' with something
        the other side of 'other'. This is possible if, for example, they
        affect different models.
        """
        MODEL_LEVEL_OPERATIONS = (
            migrations.CreateModel,
            migrations.AlterModelTable,
            migrations.AlterUniqueTogether,
            migrations.AlterIndexTogether,
        )
        FIELD_LEVEL_OPERATIONS = (
            migrations.AddField,
            migrations.AlterField,
        )
        # If it's a model level operation, let it through if there's
        # nothing that looks like a reference to us in 'other'.
        if isinstance(operation, MODEL_LEVEL_OPERATIONS):
            if not other.references_model(operation.name, app_label):
                return True
        # If it's field level, only let it through things that don't reference
        # the field (which includes not referencing the model)
        if isinstance(operation, FIELD_LEVEL_OPERATIONS):
            if not other.references_field(operation.model_name, operation.name, app_label):
                return True
        return False
